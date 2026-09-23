"""Collection checkpoints for coverage plans; uses the actual bounded transport."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dendra_coverage import digest, require, utc, validate_plan
from transport import ChunkStore, CacheError, FetchError, _atomic_json, parse_utc
from bridge import write_csv, sha


class BoundChunks(ChunkStore):
    """Complete envelopes with identity, policy and retrieval metadata bound.

    The inherited observation-content hash intentionally excludes retrieval time.
    This additional checksum binds the entire completed collection envelope.
    It is integrity checking within trusted local state, not a signed provider proof.
    """
    def __init__(self, root, task):
        super().__init__(Path(root)/'coverage-v1')
        self.task = task

    @staticmethod
    def seal(envelope):
        return digest({k: v for k, v in envelope.items()
                       if k not in ('collection_sha256', 'cache_hit')})

    def safe_path(self, path):
        # Refuse links at every component, including a dangling leaf, before writes.
        for part in (path, *path.parents):
            require(not part.is_symlink(), 'Symlink in collection state')
        return path

    def chunk_path(self, stream_id, chunk_key):
        return self.safe_path(super().chunk_path(stream_id, chunk_key))

    def validate_envelope(self, envelope):
        require(envelope.get('collection_binding') == self.task['collection_binding'] and
                envelope.get('collection_sha256') == self.seal(envelope),
                'Collection checkpoint identity/provenance integrity')
        pages = envelope['pages']
        require(0 < len(pages) == envelope['page_count'] <= 20 and
                envelope['diagnostics']['completion_reason'] in
                ('empty_page', 'short_page_with_effective_limit'), 'Incomplete source checkpoint')
        require(envelope['retrieval_first_utc'] == pages[0]['retrieved_at_utc'] and
                envelope['retrieval_last_utc'] == pages[-1]['retrieved_at_utc'],
                'Checkpoint retrieval/page binding')
        for page in pages:
            require(utc(page['requested_at_utc']) <= utc(page['retrieved_at_utc']), 'Source page clock order')
        require(utc(envelope['retrieval_first_utc']) <= utc(envelope['retrieval_last_utc']), 'Source retrieval order')
        require(envelope['requested_interval'] == {'start_inclusive': self.task['start']+'T08:00:00.000Z',
                                                  'end_exclusive': self.task['end']+'T08:00:00.000Z'}, 'Checkpoint interval binding')

    def _write_checkpoint(self, envelope, chunk_key):
        self.validate_envelope(envelope)
        self.safe_path(self.root/'checkpoints'/envelope['datastream_id']/(chunk_key+'.json'))
        return super()._write_checkpoint(envelope, chunk_key)

    def record_failure(self, stream_id, chunk_key, interval, exc, failed_at_utc):
        self.safe_path(self.root/'failures'/stream_id/(chunk_key+'.json'))
        return super().record_failure(stream_id, chunk_key, interval, exc, failed_at_utc)

    def load(self, stream_id, chunk_key):
        path = self.chunk_path(stream_id, chunk_key)
        require(not path.exists() or path.stat().st_size <= 64*1024**2, 'Oversized collection checkpoint')
        envelope = super().load(stream_id, chunk_key)
        return envelope

    def save(self, envelope, chunk_key):
        envelope['collection_binding'] = self.task['collection_binding']
        envelope['collection_sha256'] = self.seal(envelope)
        self.validate_envelope(envelope)
        return super().save(envelope, chunk_key)


def collect_coverage(plan, client, state, output):
    validate_plan(plan)
    out = Path(output); out.mkdir(parents=True, exist_ok=True)
    results, failures, deferred = [], [], []
    new_intervals = total_rows = total_bytes = 0
    exhausted = False
    for task in plan['intervals']:
        sid = task['stream']['datastream_id']
        store = BoundChunks(state, task)
        try:
            cached = store.load(sid, task['checkpoint_key'])
            if cached is None:
                if exhausted or new_intervals >= plan['limits']['new_intervals']:
                    deferred.append(dict(task_id=task['task_id'], datastream_id=sid,
                                         start=task['start'], end=task['end'], reason='bounded_run_deferred'))
                    continue
                new_intervals += 1
            envelope = client.fetcher.fetch_chunk(
                store, sid, task['start']+'T08:00:00Z', task['end']+'T08:00:00Z',
                chunk_key=task['checkpoint_key'])
            require(parse_utc(envelope['retrieval_last_utc']) <= client.now(), 'Future checkpoint retrieval')
            total_rows += len(envelope['rows'])
            require(total_rows <= plan['limits']['source_rows'], 'Completed output row budget exceeded')
            csvpath = out/(sid+'-'+task['task_id']+'.csv')
            write_csv(csvpath, envelope['rows'], task['stream'])
            total_bytes += csvpath.stat().st_size
            require(total_bytes <= plan['limits']['response_bytes'], 'Completed output byte budget exceeded')
            results.append({**task, 'native_csv': str(csvpath.resolve()), 'native_sha256': sha(csvpath),
                            'chunks': [{k: envelope[k] for k in ('content_sha256', 'requested_interval',
                                                               'retrieval_last_utc', 'latest_observation_utc')}],
                            'native_row_count': len(envelope['rows']), 'cache_hit': envelope['cache_hit'],
                            'collection_sha256': envelope['collection_sha256']})
        except Exception as exc:
            error_class = 'budget_limited' if ('budget' in str(exc).lower() or
                            'deadline' in str(exc).lower() or 'clock' in str(exc).lower()) else 'provider_failure' if isinstance(exc, FetchError) else 'checkpoint_or_identity_hold'
            exhausted |= error_class == 'budget_limited'
            failures.append(dict(task_id=task['task_id'], datastream_id=sid, start=task['start'], end=task['end'],
                                 error_class=error_class, error_type=type(exc).__name__, error=str(exc),
                                 details=getattr(exc, 'details', {})))
    completed = {item['task_id'] for item in results}
    value = dict(version='dendra-coverage-collection-1', plan_sha256=plan['plan_sha256'],
                 streams=results, catalog=plan['catalog'], failures=failures, deferred=deferred,
                 complete=len(completed) == len(plan['intervals']) and not failures and not deferred,
                 required_intervals=len(plan['intervals']), completed_intervals=len(results),
                 backlog_intervals=len(plan['intervals'])-len(completed),
                 new_intervals_attempted=new_intervals, completed_native_rows=total_rows,
                 completed_native_bytes=total_bytes, acknowledged_parent_advanced=False)
    _atomic_json(out/'native_manifest.json', value)
    _atomic_json(out/'collection-result.json', {k: v for k, v in value.items() if k not in ('streams', 'catalog')} |
                 {'completed': [{k: item[k] for k in ('task_id', 'start', 'end', 'cache_hit', 'chunks', 'collection_sha256')}
                                | {'datastream_id': item['stream']['datastream_id']} for item in results]})
    return value
