"""Execute deployment control flow against fake gcloud/verifiers; no cloud or network."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).parents[1] / 'scripts/deploy_cloud_run.sh'
PROJECT = 'qwiklabs-gcp-04-b992b9561e32'


@pytest.fixture
def deployment(tmp_path):
    root = tmp_path / 'repo'
    scripts = root / 'scripts'
    scripts.mkdir(parents=True)
    shutil.copy(SCRIPT, scripts / SCRIPT.name)
    (root / '.gitignore').write_text('gha-creds-*.json\n')
    (scripts / 'verify_live_release.py').write_text('''
import argparse,json,os
from pathlib import Path
CI_DEPLOYER = 'food-equity-deploy@qwiklabs-gcp-04-b992b9561e32.iam.gserviceaccount.com'
def resolve_service(**kwargs):
    return {}
if __name__ == '__main__':
    p=argparse.ArgumentParser()
    for k in ('candidate-tag','expected-revision','expected-commit','output','expected-deployer'):p.add_argument('--'+k)
    p.add_argument('--public',action='store_true');a=p.parse_args()
    assert not list(Path(__file__).parents[1].glob('gha-creds-*'))
    assert a.expected_commit == os.environ['HEAD_SHA']
    Path(a.output).write_text(json.dumps({'runtime_passed':True,'target':{'url':'https://candidate.run.app'}}))
    raise SystemExit(int(os.environ.get('FAIL_CORE','0')))
''')
    (scripts / 'verify_live_intake.py').write_text('''
import json,os
print(json.dumps({'passed':not bool(os.environ.get('FAIL_LIVE'))}))
raise SystemExit(int(os.environ.get('FAIL_LIVE','0')))
''')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    git('init', '-b', 'main')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Offline fixture')
    git('add', '.')
    git('commit', '-m', 'fixture')
    git('remote', 'add', 'origin', str(root))  # local transport only
    sha = git('rev-parse', 'HEAD')
    (root / 'gha-creds-ignored.json').write_text('NEVER_ARCHIVE_THIS_FIXTURE')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake = bin_dir / 'gcloud'
    fake.write_text(f'''#!{Path(sys.executable).resolve()}
import json,os,sys,subprocess
from pathlib import Path
a=sys.argv[1:]; state=Path(os.environ['FAKE_STATE']); log=Path(os.environ['FAKE_LOG'])
with log.open('a') as f:f.write(json.dumps(a)+'\\n')
def option(name):return a[a.index(name)+1]
if a[:2]==['auth','list']:print(os.environ.get('FAKE_ACCOUNT','student-fixture@qwiklabs.net'))
elif a[:3]==['config','get-value','project']:print('{PROJECT}')
elif a[:3]==['run','services','describe']:
 s=json.loads(state.read_text())
 print(json.dumps(s))
elif a[:2]==['run','deploy']:
 s=json.loads(state.read_text());rev='seattle-food-broker-'+option('--revision-suffix')
 assert '--no-traffic' in a and '--allow-unauthenticated' not in a
 assert '--source' in a and option('--source')!=os.getcwd()
 s['spec']['template']['spec']['containers'][0]['env'].append({{'name':'APP_COMMIT_SHA','value':os.environ['HEAD_SHA']}})
 s['status']['traffic'].append({{'tag':option('--tag'),'revisionName':rev,'percent':0}})
 if os.environ.get('CONFIG_DRIFT'):s['spec']['template']['spec']['containers'][0]['env'][0]['value']='changed'
 state.write_text(json.dumps(s))
 if os.environ.get('ADVANCE_MAIN'):subprocess.run(['git','commit','--allow-empty','-m','Newer main'],check=True,stdout=subprocess.DEVNULL)
elif a[:3]==['run','services','update-traffic']:
 s=json.loads(state.read_text());rev=option('--to-revisions').split('=')[0]
 assert rev.startswith('seattle-food-broker-g'+os.environ['HEAD_SHA'][:10]+'-')
 s['status']['traffic']=[{{'revisionName':rev,'percent':100}}];state.write_text(json.dumps(s))
else:raise SystemExit('unexpected fake gcloud command')
''')
    fake.chmod(0o755)
    state = tmp_path / 'state.json'
    state.write_text(json.dumps({'spec': {'template': {'spec': {
        'serviceAccountName': f'food-runtime@{PROJECT}.iam.gserviceaccount.com',
        'containers': [{'image': 'existing-image', 'env': [{'name': 'KEEP', 'value': 'unchanged'}]}],
    }}}, 'status': {'traffic': [{'revisionName': 'old-revision', 'percent': 100}]}}))
    log = tmp_path / 'calls.jsonl'
    env = dict(os.environ, PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],
               FOOD_DEPLOY_PYTHON=sys.executable, FAKE_STATE=str(state), FAKE_LOG=str(log), HEAD_SHA=sha)
    evidence = tmp_path / 'evidence'
    def run(*args, **overrides):
        result = subprocess.run(['bash', str(scripts / SCRIPT.name), '--evidence-dir', str(evidence), *args],
                                env={**env, **overrides}, capture_output=True, text=True, timeout=20)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls
    return root, run, evidence


def test_default_verifies_candidate_without_promoting(deployment):
    _, run, evidence = deployment
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert (evidence / 'core.json').exists() and (evidence / 'live-2.json').exists()
    assert not any(c[:3] == ['run','services','update-traffic'] for c in calls)
    assert 'production traffic unchanged' in result.stdout


def test_ci_promotes_exact_checked_revision_only(deployment):
    _, run, evidence = deployment
    result, calls = run('--ci', '--promote', FAKE_ACCOUNT=f'food-equity-deploy@{PROJECT}.iam.gserviceaccount.com')
    assert result.returncode == 0, result.stderr
    promotion = [c for c in calls if c[:3] == ['run','services','update-traffic']]
    assert len(promotion) == 1 and '--to-latest' not in promotion[0]
    assert (evidence / 'promoted.json').exists()


@pytest.mark.parametrize('flag', ['FAIL_CORE', 'FAIL_LIVE'])
def test_failed_candidate_check_never_promotes(deployment, flag):
    _, run, _ = deployment
    result, calls = run('--promote', **{flag:'1'})
    assert result.returncode != 0
    assert not any(c[:3] == ['run','services','update-traffic'] for c in calls)


def test_dirty_source_stops_before_cloud(deployment):
    root, run, _ = deployment
    (root / 'unreviewed.py').write_text('not committed')
    result, calls = run('--promote')
    assert result.returncode != 0 and not calls


def test_wrong_ci_account_stops_before_deploy(deployment):
    _, run, _ = deployment
    result, calls = run('--ci', '--promote', FAKE_ACCOUNT='someone@example.invalid')
    assert result.returncode != 0
    assert not any(c[:2] == ['run','deploy'] for c in calls)


@pytest.mark.parametrize('flag', ['CONFIG_DRIFT', 'ADVANCE_MAIN'])
def test_configuration_drift_or_newer_main_blocks_promotion(deployment, flag):
    _, run, _ = deployment
    result, calls = run('--ci', '--promote',
                        FAKE_ACCOUNT=f'food-equity-deploy@{PROJECT}.iam.gserviceaccount.com', **{flag:'1'})
    assert result.returncode != 0
    assert any(c[:2] == ['run','deploy'] for c in calls)
    assert not any(c[:3] == ['run','services','update-traffic'] for c in calls)
