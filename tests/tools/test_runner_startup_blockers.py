"""Real local subprocess diagnostics; no model/auth/trust operations are invoked."""
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from tools import codex_runner as runner, scenario_graph

WARNING = 'Ignoring 13 permissions.allow entries because the workspace has not been trusted'


@pytest.mark.parametrize('diagnostic,provider,kind', [
    ('Error: refusing to run in an untrusted workspace', 'claude', 'workspace_untrusted'),
    ('Error: authentication failed', 'claude', 'authentication_required'),
    (json.dumps({'type': 'error', 'error': {'message': 'invalid api key'}}), 'opencode', 'authentication_required'),
])
def test_live_blocker_stops_own_process_before_timeout(tmp_path, diagnostic, provider, kind):
    script = tmp_path / 'fake_cli.py'
    script.write_text('import sys,time\nprint(' + repr(diagnostic) + ',file=sys.stderr,flush=True)\ntime.sleep(20)\n', encoding='utf-8')
    metadata = {}
    started = time.monotonic()
    ok, reason = runner.run_once([sys.executable, str(script)], tmp_path, [], 1, 1, poll=0.02, provider=provider, timeout=8, result_metadata=metadata)
    assert time.monotonic() - started < 6
    assert not ok and reason.startswith('fatal:' + kind)
    assert metadata['failure_kind'] == kind
    assert metadata['blocked_reason']
    assert all(value is None for value in metadata['usage'].values())
    assert diagnostic in open(metadata['result_path'], encoding='utf-8').read()


def test_quoted_tool_and_assistant_content_do_not_become_blockers(tmp_path):
    records = [
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': WARNING}},
        {'type': 'item.completed', 'item': {'type': 'command_execution', 'aggregated_output': 'Error: authentication failed'}},
        {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'Explain authentication failed errors', 'permission_denials': [{'tool_name': 'Bash'}]},
    ]
    script = tmp_path / 'fake_ok.py'
    script.write_text('print(' + repr('\n'.join(map(json.dumps, records))) + ')\n', encoding='utf-8')
    ok, reason = runner.run_once([sys.executable, str(script)], tmp_path, [], 1, 1, poll=0.02, provider='claude', timeout=4)
    assert ok, reason
    assert runner.startup_blocker('Example: ' + WARNING, 'claude') == ('', '')
    assert runner.startup_blocker(WARNING, 'claude') == ('', '')
    assert runner.startup_blocker('Error: rate limit exceeded', 'claude') == ('', '')


def test_trust_blocker_is_not_retried_or_followed_by_graph_success_node(tmp_path):
    script = tmp_path / 'fake_claude.py'
    script.write_text('import sys,time\nprint(' + repr('Error: refusing to run in an untrusted workspace') + ',file=sys.stderr,flush=True)\ntime.sleep(20)\n', encoding='utf-8')
    graph = {'version': 1, 'id': 'blocked', 'scenario': 'coding', 'entry_node': 'build', 'nodes': [
        {'id': 'build', 'kind': 'task', 'provider': 'claude', 'model': 'controlled'},
        {'id': 'test', 'kind': 'task', 'provider': 'codex', 'model': None}],
        'edges': [{'id': 'next', 'source': 'build', 'target': 'test', 'condition': 'success'}]}
    args = SimpleNamespace(dispatcher=False, codex_cmd=None, retries=3, timeout=8, session_grace=1, heartbeat=1, retry_backoff=0)
    actual_run = runner.run_once
    def fast_poll(*values, **kwargs):
        return actual_run(*values, poll=0.02, **kwargs)
    with patch.object(runner, 'resolve_codex', return_value=[sys.executable, str(script)]), patch.object(runner.shutil, 'which', return_value=sys.executable), patch.object(runner, 'run_once', side_effect=fast_poll) as invocation:
        ok, reason, metadata, actual = scenario_graph.execute(graph, 'Controlled test', tmp_path, args, 'blocked-run', [0], runner.execute_with_fallback, runner.record_attempt)
    assert invocation.call_count == 1
    assert not ok
    assert metadata['graph_states'] == {'build': 'failed', 'test': 'skipped'}
    assert metadata['graph_results']['build']['reason'].startswith('fatal:workspace_untrusted')
    assert metadata['activated_edges'] == []
    events = [json.loads(line) for line in (tmp_path / 'log/events.jsonl').read_text(encoding='utf-8').splitlines()]
    attempts = [event for event in events if event.get('type') == 'model_attempt' and event['outcome'] != 'started']
    assert len(attempts) == 1
    assert attempts[0]['outcome'] == 'failed'
    assert attempts[0]['quota_exhausted_providers'] == []
    assert attempts[0]['blocked_reason']
    assert all(value is None for value in attempts[0]['usage'].values())


def test_claude_command_streams_progress_without_permission_override():
    with patch.object(runner.shutil, 'which', return_value='fake'), patch.object(runner, 'resolve_codex', return_value=['claude']):
        cmd = runner.provider_command({'provider': 'claude', 'model': 'controlled'}, 'test')
    assert cmd[cmd.index('--output-format') + 1] == 'stream-json'
    assert '--verbose' in cmd
    assert '--dangerously-skip-permissions' not in cmd


def test_warning_then_streaming_work_survives_and_timeout_keeps_partial_usage(tmp_path):
    message = {'type': 'assistant', 'message': {'id': 'unique-message', 'model': 'reported-model', 'usage': {'input_tokens': 2, 'output_tokens': 5, 'cache_read_input_tokens': 7, 'cache_creation_input_tokens': 11}}}
    script = tmp_path / 'stream.py'
    script.write_text('import time\nprint(' + repr(WARNING) + ',flush=True)\nprint(' + repr(json.dumps(message)) + ',flush=True)\nprint(' + repr(json.dumps(message)) + ',flush=True)\ntime.sleep(20)\n', encoding='utf-8')
    metadata = {}
    ok, reason = runner.run_once([sys.executable, str(script)], tmp_path, [], 1, 1, poll=0.02, provider='claude', timeout=0.3, result_metadata=metadata)
    assert not ok and 'timeout' in reason and not reason.startswith('fatal:')
    assert metadata['usage'] == {'input_tokens': 2, 'output_tokens': 5, 'cached_input_tokens': 7}
    assert metadata['cache_creation_input_tokens'] == 11
    assert metadata['usage_partial'] is True
    assert metadata['usage_source'] == 'claude_stream_partial'
    assert metadata['actual_model'] == 'reported-model'


def test_final_usage_replaces_partial_message_totals():
    messages = [
        {'type': 'assistant', 'message': {'id': 'm', 'usage': {'input_tokens': 2, 'output_tokens': 3}}},
        {'type': 'result', 'usage': {'input_tokens': 10, 'output_tokens': 20, 'cache_read_input_tokens': 5, 'cache_creation_input_tokens': 6}},
    ]
    metadata = runner.output_metadata('\n'.join(map(json.dumps, messages)))
    assert metadata['usage'] == {'input_tokens': 10, 'output_tokens': 20, 'cached_input_tokens': 5}
    assert metadata['cache_creation_input_tokens'] == 6
    assert metadata['usage_partial'] is False


def test_terminal_permission_failure_is_fatal_but_success_with_denial_is_not():
    failed = {'type': 'result', 'is_error': True, 'permission_denials': [{'tool_name': 'Write'}]}
    assert runner.startup_blocker(json.dumps(failed), 'claude')[0] == 'permission_blocked'
    successful = {**failed, 'is_error': False, 'subtype': 'success'}
    assert runner.startup_blocker(json.dumps(successful), 'claude') == ('', '')


def test_partial_same_message_updates_replace_usage_without_adding_iterations():
    entries = [
        {'type': 'assistant', 'message': {'id': 'm1', 'usage': {'input_tokens': 2, 'output_tokens': 1}}},
        {'type': 'assistant', 'message': {'id': 'm1', 'usage': {'input_tokens': 2, 'output_tokens': 3, 'iterations': [{'output_tokens': 3}], 'output_tokens_details': {'thinking_tokens': 2}}}},
        {'type': 'assistant', 'message': {'id': 'm2', 'usage': {'input_tokens': 4, 'output_tokens': 5}}},
    ]
    result = runner.output_metadata('\n'.join(map(json.dumps, entries)))
    assert result['usage'] == {'input_tokens': 6, 'output_tokens': 8, 'cached_input_tokens': None}
    assert result['usage_partial'] is True


@pytest.mark.parametrize('success_fields', [{}, {'type': 'result'}, {'type': 'result', 'is_error': False}, {'subtype': 'success', 'is_error': False}])
def test_unconfirmed_permission_denials_rejected(success_fields, tmp_path):
    payload = {**success_fields, 'result': 'ok', 'permission_denials': [{'tool': 'Write'}]}
    assert runner.startup_blocker(json.dumps(payload), 'claude')[0] == 'permission_blocked'
    path = tmp_path / 'output.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    assert runner.provider_reported_error(path) is not None
    payload.update(type='result', subtype='success', is_error=False)
    path.write_text(json.dumps(payload), encoding='utf-8')
    assert runner.provider_reported_error(path) is None
