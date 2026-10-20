from copy import deepcopy
from unittest.mock import Mock

from agent.action_state import ActionState, MAX_ACTIONS
from agent.live_intake import Draft, REJECTION_REASONS
from tests.test_view_context import view_fixture


def state():
    actions=ActionState(REJECTION_REASONS);actions.set_view(view_fixture())
    started=actions.start_turn()
    owner={k:v for k,v in started.items() if k!='type'}
    return actions,owner


def test_reads_have_no_patch_or_provider_effect_and_retain_untrusted_label():
    actions,owner=state();patch=Mock(side_effect=AssertionError('No draft mutation for reads'))
    result=actions.execute('read_recipient_evidence','call1',{**owner,'recipient_id':'3'},patch)
    assert result['outcome']=='applied' and result['effect']=='read_only'
    assert result['payload']['authority']=='untrusted_browser_projection'
    patch.assert_not_called()


def test_duplicate_call_has_one_effect_changed_arguments_are_refused():
    actions,owner=state();draft=Draft();draft.text('I have two pallets')
    patch=Mock(side_effect=draft.update)
    args={**owner,'pallet_count':2,'evidence':'I have two pallets'}
    first=actions.execute('draft_offer','same',args,patch)
    duplicate=actions.execute('draft_offer','same',args,patch)
    changed=actions.execute('draft_offer','same',{**args,'pallet_count':3},patch)
    assert first['effect']=='draft_proposed' and first['payload']['draft']['draft_version']==1
    assert duplicate['replayed'] is True
    assert changed['code']=='duplicate_call_changed' and changed['outcome']=='refused'
    assert patch.call_count==1


def test_stale_view_cross_session_and_completed_turn_never_apply():
    actions,owner=state();patch=Mock()
    other,_=state()
    assert other.execute('draft_offer','cross',owner,patch)['outcome']=='stale'
    newer=view_fixture();newer['view_revision']+=1
    actions.set_view(newer)
    assert actions.execute('draft_offer','stale',owner,patch)['outcome']=='stale'
    actions,owner=state();actions.finish()
    assert actions.execute('draft_offer','finished',owner,patch)['outcome']=='stale'
    patch.assert_not_called()


def test_cancelled_action_cannot_apply_and_next_turn_can_recover():
    actions,owner=state();draft=Draft();draft.text('Two crates')
    result=actions.execute('draft_offer','bad',{**owner,'pallet_count':2,'evidence':'Two crates'},draft.update)
    assert result['outcome']=='refused' and not result['accepted']
    actions.cancel()
    assert actions.execute('draft_offer','cancelled',owner,draft.update)['outcome']=='cancelled'
    started=actions.start_turn();owner={k:v for k,v in started.items() if k!='type'}
    draft.text('Two pallets')
    result=actions.execute('draft_offer','good',{**owner,'pallet_count':2,'evidence':'Two pallets'},draft.update)
    assert result['outcome']=='applied' and draft.values['pallet_count']==2


def test_replay_registry_is_bounded_without_eviction_or_reexecution():
    actions,owner=state();patch=Mock()
    for i in range(MAX_ACTIONS):
        assert actions.execute('read_current_match',str(i),owner,patch)['outcome']=='applied'
    assert actions.execute('read_current_match','extra',owner,patch)['code']=='action_limit'
    assert len(actions.calls)==MAX_ACTIONS
    assert actions.execute('read_current_match','0',owner,patch)['replayed']
    patch.assert_not_called()
