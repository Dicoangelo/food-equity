"""Per-WebSocket turn/call ownership. No durable identity, cache, or external actions."""
import hashlib
import json
from uuid import uuid4

from pydantic import Field, ValidationError
from agent.view_context import StrictModel, ViewContext, read_view, recipient_id

MAX_ACTIONS = 30


class Owner(StrictModel):
    turn_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.:-]+$')
    expected_offer_revision: int = Field(ge=0, le=1000000, strict=True)
    expected_view_revision: int = Field(ge=0, le=1000000, strict=True)


class ReadArgs(Owner):
    pass


class RecipientArgs(Owner):
    recipient_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.:-]+$')


class ActionState:
    def __init__(self, patch_errors=None):
        self.view = ViewContext()
        self.view_received = False
        self.owner = None
        self.active = False
        self.cancelled = False
        self.calls = {}
        self.patch_errors = patch_errors or {}

    def set_view(self, raw):
        view = ViewContext.model_validate(raw)
        if self.view_received:
            if view.view_revision < self.view.view_revision or view.offer_revision < self.view.offer_revision:
                raise ValueError('Stale view context')
            if view.view_revision == self.view.view_revision:
                if view != self.view:
                    raise ValueError('Changed content requires a new view revision')
                return False
        self.view, self.view_received = view, True
        return True

    def start_turn(self):
        if self.active:
            raise ValueError('The previous turn is still active')
        self.owner = Owner(turn_id='turn-'+uuid4().hex[:24], expected_offer_revision=self.view.offer_revision,
                           expected_view_revision=self.view.view_revision)
        self.active, self.cancelled = True, False
        return {'type':'turn_started', **self.owner.model_dump()}

    def cancel(self):
        self.cancelled, self.active = True, False

    def finish(self):
        self.active = False

    def result(self, call_id, owner, outcome, message, **extra):
        return {'type':'action_result', 'call_id':call_id,
                **(owner.model_dump() if owner else {
                    'turn_id':None,'expected_offer_revision':None,'expected_view_revision':None}),
                'outcome':outcome, 'effect':'none', 'resulting_revision':None,
                'accepted':outcome == 'applied', 'message':message, **extra}

    def execute(self, name, call_id, raw, apply_patch):
        # Registry is scoped to this WebSocket and never evicts a live ID for reuse.
        if not isinstance(call_id,str) or not 1 <= len(call_id) <= 128:
            return self.result(None,self.owner,'refused','Action refused: a bounded call identifier is required.',code='invalid_call_id')
        try:
            encoded=json.dumps({'name':name,'args':raw},sort_keys=True,allow_nan=False)
            if len(encoded.encode()) > 16384:
                raise ValueError
            digest=hashlib.sha256(encoded.encode()).hexdigest()
            if not isinstance(raw,dict):
                raise ValueError
            owner=Owner.model_validate({k:raw.get(k) for k in Owner.model_fields})
        except (ValueError,TypeError):
            return self.result(call_id,self.owner,'refused','Action refused: valid turn and view ownership are required.',code='invalid_action_owner')
        previous=self.calls.get(call_id)
        if previous and previous['digest'] != digest:
            return self.result(call_id,owner,'refused','Action refused: this call identifier was already used for different arguments.',code='duplicate_call_changed')
        if self.cancelled and owner == self.owner:
            result = self.result(call_id,owner,'cancelled','Action cancelled with its interrupted turn. Continue with the next input.',code='turn_cancelled')
            # Preserve IDs observed during retirement across provider replacement.
            if not previous and len(self.calls) < MAX_ACTIONS:
                self.calls[call_id] = {'digest':digest,'result':result}
            return result
        if (not self.active or owner != self.owner or owner.expected_offer_revision != self.view.offer_revision
                or owner.expected_view_revision != self.view.view_revision):
            return self.result(call_id,owner,'stale','Action ignored because its turn or displayed view is no longer current.',code='stale_action')
        if previous:
            return {**previous['result'], 'replayed':True}
        if len(self.calls) >= MAX_ACTIONS:
            return self.result(call_id,owner,'refused','This session reached its bounded action limit.',code='action_limit')
        try:
            if name == 'draft_offer':
                patch={k:v for k,v in raw.items() if k not in Owner.model_fields}
                snapshot=apply_patch(patch)
                result=self.result(call_id,owner,'applied',
                    'Draft proposal updated. The form has not changed: review and Apply, then Run matching explicitly.',
                    effect='draft_proposed',resulting_revision=snapshot['draft_version'],payload={'draft':snapshot})
            elif name in ('read_current_match','read_recipient_evidence','show_alternatives'):
                args=(RecipientArgs if name == 'read_recipient_evidence' else ReadArgs).model_validate(raw)
                payload=read_view(self.view,name,getattr(args,'recipient_id',None))
                outcome=payload.pop('outcome')
                message=payload.pop('message')
                payload.pop('effect',None)
                result=self.result(call_id,owner,outcome,message,
                    effect='read_only' if outcome == 'applied' else 'none',
                    resulting_revision=self.view.view_revision if outcome == 'applied' else None,payload=payload)
            else:
                result=self.result(call_id,owner,'refused','Unsupported action. Only draft proposals and bounded display reads are available.',code='unsupported_tool')
        except (ValueError,TypeError) as exc:
            code=getattr(exc,'code','invalid_patch')
            if code in self.patch_errors:
                field,message=self.patch_errors[code]
            else:
                code,field,message='invalid_action',None,'Action refused: the bounded argument schema was not satisfied.'
            result=self.result(call_id,owner,'refused',message,code=code,field=field)
        self.calls[call_id]={'digest':digest,'result':result}
        return result
