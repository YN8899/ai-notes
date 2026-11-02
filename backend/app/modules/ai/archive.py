"""Portable history fields. Pure validation is shared with the standalone verifier."""
from app.modules.ai.models import AiToolCall
from app.modules.ai.history_contract import RUN_FIELDS, TOOL_FIELDS, validate_run, validate_tool

FIELDS = {AiToolCall: ('id','created_at','owner_id','run_id','step_id','position','provider_call_id',
    'tool_name','arguments','result','sources','status','error_code','finished_at')}
RUN_RESTORE_DEFAULTS = {'provider': 'deepseek', 'provider_snapshot': None, 'tools': [], 'tool_scope': None,
    'auth_session_id': None}
