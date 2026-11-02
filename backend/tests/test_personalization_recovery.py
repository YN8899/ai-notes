"""Real empty-target v5 restore converts an unknown paid state to terminal history."""
from uuid import UUID
from sqlalchemy import func,select
from app.modules.ai.models import AiRun
from app.modules.personalization.models import LearningAnalysisPreview
from app.shared.models import AuthSession
from conftest import mutate
from test_social import people
from test_personalization import model,save,confirm
from test_archive_restore import restore_tool,restore_database,target,_restore,_rewrite_archive


def test_unknown_analysis_is_restored_terminal_without_preview_session_or_replay(people,model,restore_tool,target,tmp_path):
    alice=people[0]['alice'];save(alice,ai_analysis_enabled=True)
    created,_=confirm(alice);assert created.status_code==201
    mutate(alice,'POST','/personalization/analysis/'+created.json()['id']+'/cancel',{})
    response=mutate(alice,'POST','/exports/archive',{});assert response.status_code==200
    original=tmp_path/'terminal-v5.zip';original.write_bytes(response.content)
    unknown=tmp_path/'unknown-v5.zip'
    def interrupted(manifest,rows):
        run=next(row for row in rows['ai_runs'] if row['id']==created.json()['run_id'])
        run.update(status='running',finished_at=None)
    _rewrite_archive(original,unknown,interrupted)
    assert restore_tool._load_verifier().verify_archive(unknown)['learning_analyses']==1
    _restore(restore_tool,unknown,target)
    with target.sessions() as db:
        run=db.get(AiRun,UUID(created.json()['run_id']))
        assert run.status=='interrupted' and run.error_code=='restore_not_resumed'
        assert run.auth_session_id is None and run.input_messages==[] and run.lease_until is None
        assert db.scalar(select(func.count()).select_from(AuthSession))==0
        assert db.scalar(select(func.count()).select_from(LearningAnalysisPreview))==0
