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
module.exports={currentRunEvent,validatedTaskResult};
