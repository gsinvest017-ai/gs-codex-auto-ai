// Pure historical/current delivery-envelope validation. No filesystem access:
// delivery hashes describe the recorded snapshot; later edits do not erase history.
function currentRunEvent(event, run) {
  return !!run && !!event && event.run_id === run.run_id && Number.isFinite(run.started_at)
    && Date.parse(event.ts || event.timestamp || '') >= run.started_at * 1000;
}
function validatedTaskResult(result, run, exitCode = null) {
  if (!run || !result || result.schema_version !== 1 || result.run_id !== run.run_id
      || !Number.isFinite(run.started_at) || !Number.isFinite(result.started_at) || result.started_at < run.started_at
      || !Number.isFinite(result.ended_at) || result.ended_at < result.started_at
      || !['completed', 'blocked', 'incomplete', 'failed'].includes(result.status)) return null;
  if (result.status !== 'completed') return result;
  const evidence=result.completion_evidence;
  if ((exitCode === null || exitCode === 0) && evidence && currentRunEvent(evidence,run)
      && (evidence.event_type || evidence.type) === 'phase_end'
      && String(evidence.phase).replace(/^phase/,'') === '7' && evidence.status === 'success'
      && Array.isArray(evidence.artifacts) && evidence.artifacts.length > 0
      && evidence.artifacts.every(a => a && typeof a === 'object' && typeof a.path === 'string' && a.path
        && typeof a.sha256 === 'string' && /^[a-f0-9]{64}$/i.test(a.sha256))) return result;
  return null;
}
function validatedGraphResult(graph, run, exitCode = null) {
  if (!run || !run.route || !Number.isFinite(run.started_at) || (exitCode !== null && exitCode !== 0)) return null;
  try { return (graph && graph.schema_version===1 && graph.run_id===run.run_id && graph.graph_id===run.route.graph_id && graph.graph_digest===run.route.graph_digest && /^[a-f0-9]{64}$/.test(graph.graph_digest) && Number.isFinite(graph.started_at) && Number.isFinite(graph.ended_at) && graph.started_at>=run.started_at && graph.ended_at>=graph.started_at && ['completed','blocked'].includes(graph.status) && graph.task_delivery_verified===false && graph.graph_states && Array.isArray(run.route.graph_snapshot?.nodes) && Object.keys(graph.graph_states).length===run.route.graph_snapshot.nodes.length && run.route.graph_snapshot.nodes.every(n=>Object.prototype.hasOwnProperty.call(graph.graph_states,n.id) && ['ok','skipped','failed','quota_exhausted'].includes(graph.graph_states[n.id])) && Array.isArray(graph.activated_edges) && graph.activated_edges.every(id=>run.route.graph_snapshot.edges.some(e=>e.id===id)) && (graph.status!=='completed' || (Object.values(graph.graph_states).includes('ok') && run.route.graph_snapshot.nodes.every(n=>!['failed','quota_exhausted'].includes(graph.graph_states[n.id]) || run.route.graph_snapshot.edges.some(e=>e.source===n.id && graph.activated_edges.includes(e.id) && e.condition==='quota_exhausted' && graph.graph_states[n.id]==='quota_exhausted'))))) ? graph : null; } catch { return null; }
}
module.exports={currentRunEvent,validatedTaskResult,validatedGraphResult};
