import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from tools import model_router, scenario_graph, codex_runner


def graph():
    return {'version': 1, 'id': 'example', 'scenario': 'coding', 'entry_node': 'review', 'nodes': [
        {'id': 'review', 'kind': 'review', 'provider': 'codex', 'model': None, 'task_text': 'Review'},
        {'id': 'build', 'kind': 'task', 'provider': 'claude', 'model': None, 'task_text': 'Build', 'expects': ['result.txt']}],
        'edges': [{'id': 'handoff', 'source': 'review', 'target': 'build', 'condition': 'success', 'label': 'review result'}]}


def test_presets_keep_defaults_and_require_opencode_optin(tmp_path):
    model_router.apply_preset(tmp_path, 'codex-first')
    assert model_router.resolve_route('coding', tmp_path, provider='opencode')['provider'] == 'codex'
    with pytest.raises(ValueError):
        model_router.apply_preset(tmp_path, 'opencode-first')
    model_router.apply_preset(tmp_path, 'opencode-first', 'vendor/model')
    route = model_router.resolve_route('coding', tmp_path)
    assert route['provider'] == 'opencode' and route['fallback_chain'] == []
    assert model_router.catalog(tmp_path)['fallback']['condition'] == 'disabled-for-explicit-primary'
    model_router.save_route(tmp_path, 'coding', 'opencode', 'vendor/other')
    assert model_router.resolve_route('coding', tmp_path)['model'] == 'vendor/other'
    model_router.apply_preset(tmp_path, 'claude-first')
    assert model_router.resolve_route('research', tmp_path)['provider'] == 'claude'
    model_router.apply_preset(tmp_path, 'review-codex-build-claude')
    assert scenario_graph.get(tmp_path, 'review-codex-build-claude')['execution_order'] == ['review', 'build']


def test_graph_atomic_crud_and_digest(tmp_path):
    item = scenario_graph.save(tmp_path, graph())
    assert scenario_graph.get(tmp_path, 'example') == item
    before = (tmp_path / 'log/scenario-graphs.json').read_bytes()
    invalid = graph(); invalid['edges'].append({'id': 'cycle', 'source': 'build', 'target': 'review', 'condition': 'success'})
    with pytest.raises(ValueError):
        scenario_graph.save(tmp_path, invalid)
    assert (tmp_path / 'log/scenario-graphs.json').read_bytes() == before
    item['nodes'][0]['x'] = 42
    updated = scenario_graph.save(tmp_path, item)
    assert updated['nodes'][0]['x'] == 42
    assert scenario_graph.digest(updated) != scenario_graph.digest(graph())
    assert scenario_graph.delete(tmp_path, 'example')['graphs'] == []


@pytest.mark.parametrize('path', ['../evil', '..\\evil', 'C:\\evil', '/evil'])
def test_cross_platform_artifact_escape_rejected(path):
    item = graph(); item['nodes'][1]['expects'] = [path]
    with pytest.raises(ValueError):
        scenario_graph.validate(item)


def test_real_executor_handoff_events_and_artifacts(tmp_path):
    item = scenario_graph.save(tmp_path, graph())
    calls, events = [], []
    def fake(cmd, cwd, expects, grace, heartbeat, **kwargs):
        calls.append((kwargs['provider'], cmd))
        out = tmp_path / ('response-' + str(len(calls)) + '.jsonl')
        out.write_text('{"type":"item.completed","item":{"type":"agent_message","text":"Reviewed evidence"}}\n', encoding='utf-8')
        kwargs['result_metadata'].update(result_path=str(out))
        if expects:
            (tmp_path / expects[0]).write_text('implemented', encoding='utf-8')
        return True, 'ok'
    args = SimpleNamespace(dispatcher=True, codex_cmd=None, retries=1, timeout=10, session_grace=1, heartbeat=1, retry_backoff=0)
    with patch.object(codex_runner, 'run_once', fake), patch.object(codex_runner, 'record_attempt', lambda root, event: events.append(event)), patch.object(codex_runner, 'resolve_codex', lambda provider: [provider]), patch.object(codex_runner.shutil, 'which', return_value='fake'), patch.object(codex_runner.subprocess, 'Popen', side_effect=AssertionError('paid call forbidden')):
        ok, reason, result, actual = scenario_graph.execute(item, 'Original task', tmp_path, args, 'run', [0], codex_runner.execute_with_fallback, lambda root, event: events.append(event))
    assert ok
    assert [provider for provider, cmd in calls] == ['codex', 'claude']
    assert any('Reviewed evidence' in part for part in calls[1][1])
    started = [event for event in events if event['outcome'] == 'started']
    assert [event['role'] for event in started] == ['worker', 'writer']
    assert all(event['graph_digest'] == scenario_graph.digest(item) for event in started)
    assert result['activated_edges'] == ['handoff']


@pytest.mark.parametrize('failure,condition,expected', [('quota_exhausted:test', 'quota_exhausted', True), ('network error', 'quota_exhausted', False), ('fatal:auth', 'success', False)])
def test_condition_edges_are_actual_outcomes(tmp_path, failure, condition, expected):
    item = graph(); item['edges'][0]['condition'] = condition
    def execute(route, prompt, cwd, args, run_id, counter, expects, exhausted):
        if route['graph_node_id'] == 'review':
            return False, failure, {}, route
        (tmp_path / 'result.txt').write_text('done')
        return True, 'ok', {}, route
    ok, reason, result, actual = scenario_graph.execute(item, 'task', tmp_path, SimpleNamespace(), 'run', [0], execute, lambda *a: None)
    assert ok is expected
    assert result['graph_states']['build'] == ('ok' if expected else 'skipped')


def test_missing_artifacts_cannot_complete_graph(tmp_path):
    ok, _, result, _ = scenario_graph.execute(graph(), 'task', tmp_path, SimpleNamespace(), 'run', [0], lambda route, *a: (True, 'ok', {}, route), lambda *a: None)
    assert not ok and result['graph_states']['build'] == 'failed'

def test_explicit_primary_cannot_borrow_saved_authorization_for_override(tmp_path):
    model_router.apply_preset(tmp_path, 'claude-first', 'model-a')
    with pytest.raises(ValueError, match='override differs'):
        model_router.resolve_route('coding', tmp_path, provider='claude', model='model-b')
    with pytest.raises(ValueError, match='override differs'):
        model_router.resolve_route('coding', tmp_path, provider='opencode', model='vendor/model')


def test_readonly_codex_and_unsupported_opencode_review():
    with patch.object(codex_runner.shutil, 'which', return_value='fake'), patch.object(codex_runner, 'resolve_codex', return_value=['codex']):
        cmd = codex_runner.provider_command({'provider': 'codex', 'model': None, 'read_only': True}, 'review')
    assert cmd[cmd.index('--sandbox') + 1] == 'read-only'
    item = graph(); item['opencode_opt_in'] = True
    item['nodes'][0].update(provider='opencode', model='vendor/model')
    with pytest.raises(ValueError, match='read-only'):
        scenario_graph.validate(item)


def test_graph_digest_rejects_changed_preview_without_call(tmp_path, capsys):
    scenario_graph.save(tmp_path, graph())
    with patch.object(codex_runner, 'execute_with_fallback', side_effect=AssertionError('must not call')):
        assert codex_runner.main(['--cwd', str(tmp_path), '--prompt', 'task', '--graph-id', 'example', '--graph-digest', 'stale']) == 1
    assert 'graph changed since preview' in capsys.readouterr().out

def test_preset_preserves_custom_graph_in_same_scenario(tmp_path):
    custom = scenario_graph.save(tmp_path, graph())
    model_router.apply_preset(tmp_path, 'review-codex-build-claude')
    assert scenario_graph.get(tmp_path, 'example') == custom
    assert {item['id'] for item in scenario_graph.load(tmp_path)['graphs']} == {'example', 'review-codex-build-claude'}
    with pytest.raises(ValueError, match='specify graph-id'):
        scenario_graph.get(tmp_path, scenario='coding')
    updated = copy.deepcopy(custom)
    updated['nodes'][0]['label'] = 'Updated by explicit ID'
    scenario_graph.save(tmp_path, updated)
    assert len(scenario_graph.load(tmp_path)['graphs']) == 2
    assert scenario_graph.get(tmp_path, 'example')['nodes'][0]['label'] == 'Updated by explicit ID'

@pytest.mark.parametrize('provider', ['codex', 'claude', 'opencode'])
def test_graph_worker_process_local_instructions(provider):
    route = {'provider': provider, 'model': 'vendor/model' if provider == 'opencode' else None, 'role': 'writer', 'routing_policy': 'explicit-graph', 'graph_id': 'example', 'graph_node_id': 'build', 'scenario': 'coding'}
    with patch.object(codex_runner.shutil, 'which', return_value='fake'), patch.object(codex_runner, 'resolve_codex', return_value=[provider]):
        cmd = codex_runner.provider_command(route, 'Node task')
    joined = ' '.join(cmd)
    assert 'one bounded node' in joined
    assert 'Do not start the seven-phase pipeline' in joined
    assert 'saved explicit-primary or explicit-graph selection takes precedence' in joined
    if provider == 'codex':
        assert any(item.startswith('developer_instructions=') for item in cmd)


def test_claude_dispatcher_receives_selected_policy_and_no_forced_codex():
    route = {'provider': 'claude', 'model': 'selected-model', 'role': 'dispatcher', 'scenario': 'coding', 'routing_policy': 'explicit-primary'}
    with patch.object(codex_runner.shutil, 'which', return_value='fake'), patch.object(codex_runner, 'resolve_codex', return_value=['claude']):
        cmd = codex_runner.provider_command(route, 'Build task')
    instruction = cmd[cmd.index('--append-system-prompt') + 1]
    assert 'explicit-primary' in instruction and 'selected-model' in instruction
    assert 'do not hardcode --provider codex' in instruction
    assert 'takes precedence for this invocation' in instruction
    assert 'Preserve all sandbox' in instruction

def test_binding_custom_catalog_preview_and_cross_scenario(tmp_path):
    item = graph(); item['scenario'] = 'custom-scene'
    scenario_graph.save(tmp_path, item)
    assert 'custom-scene' in [route['name'] for route in model_router.catalog(tmp_path)['scenarios']]
    model_router.bind_graph(tmp_path, '3d_modeling', 'example')
    route = model_router.resolve_route('Create a 3d mesh', tmp_path)
    assert route['execution_mode'] == 'graph'
    assert route['graph_id'] == 'example'
    assert route['scenario'] == '3d_modeling'
    assert route['graph_scenario'] == 'custom-scene'
    assert route['graph_digest'] == scenario_graph.digest(item)
    assert route['graph_execution_order'] == ['review', 'build']
    assert [node['provider'] for node in route['graph_nodes']] == ['codex', 'claude']
    model_router.bind_graph(tmp_path, 'custom-scene', 'example', ['ornament'])
    assert model_router.resolve_route('ornament', tmp_path)['scenario'] == 'custom-scene'
    model_router.apply_preset(tmp_path, 'claude-first')
    assert model_router.resolve_route('ornament', tmp_path)['execution_mode'] == 'graph'


def test_binding_delete_and_missing_graph_fail_closed(tmp_path):
    scenario_graph.save(tmp_path, graph())
    model_router.bind_graph(tmp_path, 'coding', 'example')
    before = (tmp_path / 'log/scenario-graphs.json').read_bytes()
    with pytest.raises(ValueError, match='unbind'):
        scenario_graph.delete(tmp_path, 'example')
    assert (tmp_path / 'log/scenario-graphs.json').read_bytes() == before
    with pytest.raises(ValueError, match='cannot be overridden'):
        model_router.resolve_route('coding', tmp_path, model='other')
    # Simulate external file loss; never silently resolve the default provider.
    (tmp_path / 'log/scenario-graphs.json').write_text('{"version":1,"graphs":[]}', encoding='utf-8')
    with pytest.raises(ValueError, match='bound graph missing'):
        model_router.resolve_route('coding', tmp_path)
    model_router.bind_graph(tmp_path, 'coding', None)
    assert model_router.resolve_route('coding', tmp_path)['execution_mode'] == 'route'


def test_binding_rejects_invalid_input_atomically(tmp_path):
    scenario_graph.save(tmp_path, graph())
    model_router.apply_preset(tmp_path, 'codex-first')
    config = tmp_path / 'log/model-routing.json'
    before = config.read_bytes()
    for scenario, graph_id, keywords in [('coding', 'missing', None), ('../bad', 'example', None), ('coding', 'example', [''])]:
        with pytest.raises(ValueError):
            model_router.bind_graph(tmp_path, scenario, graph_id, keywords)
        assert config.read_bytes() == before


def test_normal_dispatcher_uses_bound_graph_executor(tmp_path, capsys):
    import json
    scenario_graph.save(tmp_path, graph())
    model_router.bind_graph(tmp_path, '3d_modeling', 'example')
    calls = []
    def execute(graph, prompt, cwd, args, run_id, counter, executor, recorder):
        calls.append((graph['id'], args.graph_digest, args.dispatcher, args.binding_scenario))
        return True, 'graph completed', {'graph_digest': scenario_graph.digest(graph), 'graph_states': {'review': 'ok', 'build': 'ok'}, 'activated_edges': ['handoff']}, {'provider': 'claude'}
    with patch.object(scenario_graph, 'execute', execute), patch.object(codex_runner, 'provider_command', side_effect=AssertionError('must not launch default dispatcher')):
        assert codex_runner.main(['--cwd', str(tmp_path), '--prompt', 'Create 3d mesh', '--dispatcher']) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == [('example', scenario_graph.digest(graph()), False, '3d_modeling')]
    envelope = json.loads(Path(result['graph_result_path']).read_text(encoding='utf-8'))
    assert envelope['binding_scenario'] == '3d_modeling'
    assert envelope['graph_definition_scenario'] == 'coding'
    assert not list((tmp_path / 'log').glob('task-result-*.json'))

def test_bound_default_keeps_regular_scenario_fallback_catalog(tmp_path):
    scenario_graph.save(tmp_path, graph())
    model_router.apply_preset(tmp_path, 'codex-first')
    model_router.save_route(tmp_path, None, None, None, 'vendor/model')
    model_router.bind_graph(tmp_path, 'default', 'example')
    catalog = model_router.catalog(tmp_path)
    assert catalog['fallback'] == {'provider': 'opencode', 'model': 'vendor/model', 'condition': 'both-primary-exhausted'}
    route = model_router.resolve_route('coding', tmp_path)
    assert route['execution_mode'] == 'route'
    assert route['fallback_chain'][-1]['model'] == 'vendor/model'


def test_activity_exposes_graph_node_and_model_provenance(tmp_path):
    import json
    from tools.workbench import Workbench
    log = tmp_path / 'log'; log.mkdir()
    output = log / 'controlled.jsonl'
    output.write_text(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Findings', 'id': 'item'}}) + '\n', encoding='utf-8')
    event = {'type': 'model_attempt', 'run_id': 'run', 'attempt_id': 'run:1', 'outcome': 'ok', 'actual_provider': 'codex', 'actual_model': None, 'configured_model': 'configured-test-model', 'graph_id': 'custom', 'graph_node_id': 'review', 'graph_node_label': 'Review requirements', 'binding_scenario': '3d_modeling', 'graph_definition_scenario': 'custom-scene', 'result_path': str(output)}
    (log / 'events.jsonl').write_text(json.dumps(event) + '\n', encoding='utf-8')
    activity = Workbench(tmp_path).activity()
    item = activity['items'][0]
    assert item['graph_node_label'] == 'Review requirements'
    assert item['configured_model'] == 'configured-test-model'
    assert item['actual_model'] is None
    assert item['binding_scenario'] == '3d_modeling'

def test_binding_preview_node_order_matches_execution_not_storage(tmp_path):
    item = graph(); item['nodes'].reverse()
    scenario_graph.save(tmp_path, item)
    model_router.bind_graph(tmp_path, 'coding', 'example')
    route = model_router.resolve_route('coding', tmp_path)
    assert [node['id'] for node in route['graph_nodes']] == route['graph_execution_order'] == ['review', 'build']


def test_binding_preview_and_catalog_include_exact_conditional_edges(tmp_path):
    item = graph()
    item['edges'][0].update(condition='quota_exhausted', label='Only explicit quota exhaustion')
    scenario_graph.save(tmp_path, item)
    model_router.bind_graph(tmp_path, 'coding', 'example')
    route = model_router.resolve_route('coding', tmp_path)
    assert route['graph_edges'] == item['edges']
    catalog_route = next(route for route in model_router.catalog(tmp_path)['scenarios'] if route['name'] == 'coding')
    assert catalog_route['graph_edges'] == item['edges']
