"""Direct context handoff never invokes a model or invents successful attachment."""
from copy import deepcopy
from unittest.mock import patch

from helpers import make_context, temp_dir
from test_idea_collaboration import API as LibraryAPI
from test_ideas import expect_error, setup

from litradar import llm
from litradar.zotero import ZoteroUnavailable
from litradar.zotero_context import ENDPOINT, check_papers, discuss


class API(LibraryAPI):
    def __init__(self):
        super().__init__()
        self.available = True
        self.corrupt = False
        self.pending = False
        self.receipt = None

    def get(self, path):
        if path == ENDPOINT:
            if not self.available:
                raise ZoteroUnavailable('HTTP 404')
            return {'provider': 'llm-for-zotero', 'max_items': 30}
        if path.startswith(ENDPOINT + '?'):
            result = deepcopy(self.receipt)
            result['status'] = 'attached'
            for row in result['items']:
                row['status'] = 'attached'
            return result
        return super().get(path)

    def write(self, path, body, **kwargs):
        if path != ENDPOINT:
            return super().write(path, body, **kwargs)
        self.writes.append((path, deepcopy(body), kwargs))
        self.receipt = {'provider': 'llm-for-zotero', **body, 'status': 'opening' if self.pending else 'attached',
                        'conversation_id': 'new-chat', 'items': [
                            {'key': key, 'title': 'A paper', 'status': 'pending' if self.pending else 'attached', 'reason': 'receipt'}
                            for key in body['keys']]}
        if self.corrupt:
            self.receipt['items'][0]['key'] = 'OTHER000'
        return deepcopy(self.receipt)


def test_discuss_imports_once_without_task_files_or_model_calls():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        repo.update(idea_id, {'question': ''})  # Attaching context doesn't require a prompt.
        selected = list(repo.get(idea_id)['members'])
        api = API()
        with patch.object(llm, 'chat_json', side_effect=AssertionError('No LLM')):
            result = discuss(ctx.config, ctx.paths, idea_id, selected, api)
            assert result['status'] == 'attached'
            assert result['items'][0]['paper_id'] == selected[0]
            assert repo.detail(idea_id)['papers'][0]['zotero_key'] == 'ITEM0000'
            discuss(ctx.config, ctx.paths, idea_id, selected, api)
        assert len(api.items) == 1 and api.collections == []
        assert not repo._path('handoffs').exists()
        assert not list(tmp.rglob('*.md'))
        assert all(path.endswith('/items') or path == ENDPOINT for path, _, _ in api.writes)
        ctx.close()


def test_discuss_preflight_and_invalid_selection_do_not_mutate_zotero():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        selected = list(repo.get(idea_id)['members'])
        api = API()
        for ids in (None, [], 'bad', [{}], selected * 2, ['unknown'], selected * 31):
            expect_error(lambda ids=ids: discuss(ctx.config, ctx.paths, idea_id, ids, api))
        api.available = False
        expect_error(lambda: discuss(ctx.config, ctx.paths, idea_id, selected, api), ZoteroUnavailable)
        assert not api.writes
        ctx.close()


def test_discuss_rejects_wrong_receipt_and_does_not_resend():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        api = API()
        api.corrupt = True
        expect_error(lambda: discuss(ctx.config, ctx.paths, idea_id, list(repo.get(idea_id)['members']), api), ZoteroUnavailable)
        assert len([w for w in api.writes if w[0] == ENDPOINT]) == 1
        ctx.close()


def test_discuss_polls_the_same_request_and_preserves_original_collections():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        api = API()
        api.pending = True
        api.items['EXISTING'] = {'key': 'EXISTING', 'data': {'title': 'Prediction methods', 'DOI': '10.1/a', 'collections': ['PERSONAL']}}
        with patch('litradar.zotero_context.time.sleep'):
            result = discuss(ctx.config, ctx.paths, idea_id, list(repo.get(idea_id)['members']), api)
        assert result['status'] == 'attached'
        assert len(api.writes) == 1 and api.writes[0][0] == ENDPOINT
        assert api.items['EXISTING']['data']['collections'] == ['PERSONAL']
        ctx.close()


def test_discuss_refuses_mismatched_saved_identity_before_handoff():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        selected = list(repo.get(idea_id)['members'])
        api = API()
        discuss(ctx.config, ctx.paths, idea_id, selected, api)
        api.items['ITEM0000']['data']['DOI'] = '10.1/wrong'
        api.writes.clear()
        expect_error(lambda: discuss(ctx.config, ctx.paths, idea_id, selected, api), ZoteroUnavailable)
        assert api.writes == []
        ctx.close()


def test_check_papers_reports_missing_unknown_and_local_files_without_writes():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        selected = list(repo.get(idea_id)['members'])
        api = API()
        original = deepcopy(repo.detail(idea_id))
        result = check_papers(ctx.config, ctx.paths, idea_id, selected, api)
        assert result['items'][0]['state'] == 'missing'
        api.items['EXISTING'] = {'key': 'EXISTING', 'data': {'title': 'Prediction methods', 'DOI': '10.1/a'}}
        assert check_papers(ctx.config, ctx.paths, idea_id, selected, api)['items'][0]['state'] == 'unknown'
        original_get = api.get
        for exists, expected in ((False, 'missing'), (True, 'ready')):
            with patch.object(api, 'get', side_effect=lambda path, exists=exists: {'contextAvailable': exists}
                              if '/litradar/files?' in path else original_get(path)):
                assert check_papers(ctx.config, ctx.paths, idea_id, selected, api)['items'][0]['state'] == expected
        assert not api.writes
        assert repo.detail(idea_id) == original
        ctx.close()


def test_discuss_progress_waits_for_receipt_and_reports_selected_count():
    with temp_dir() as tmp:
        ctx = make_context(tmp)
        repo, idea_id = setup(ctx)
        selected = list(repo.get(idea_id)['members'])
        api = API()
        api.pending = True
        updates = []
        with patch('litradar.zotero_context.time.sleep'):
            result = discuss(ctx.config, ctx.paths, idea_id, selected, api,
                             progress=lambda *event: updates.append(event))
        assert updates[0][0] == '检查 Zotero 与讨论插件'
        assert updates[-1] == ('等待 Zotero 确认上下文', 0, len(selected))
        assert all(total == len(selected) and 0 <= completed <= total for _, completed, total in updates)
        assert result['status'] == 'attached'
        assert len([w for w in api.writes if w[0] == ENDPOINT]) == 1
        ctx.close()
