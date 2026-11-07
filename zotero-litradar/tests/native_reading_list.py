"""Real Zotero/Reading List integration in a fresh temporary profile. macOS runner."""
from pathlib import Path
import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'src'))
from litradar.config import Config
from litradar.model import Article
from litradar.zotero_workflow import LocalAPI,ensure_item,lookup,set_status,reading_list_payload,migrate_legacy,TODO_TAG,READ_TAG,ZoteroUnavailable


def wait_json(path, process, timeout=45):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if path.exists():
            value=json.loads(path.read_text())
            if 'error' in value: raise RuntimeError(value)
            return value
        if process.poll() is not None: raise RuntimeError('Zotero exited; inspect native.log')
        time.sleep(.2)
    raise TimeoutError(str(path))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--zotero',default='/Applications/Zotero.app/Contents/MacOS/zotero')
    parser.add_argument('--reading-list',required=True)
    args=parser.parse_args()
    root=Path(tempfile.mkdtemp(prefix='litradar-reading-native-'))
    profile=root/'profile';(profile/'extensions').mkdir(parents=True)
    data=root/'data';data.mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    prefs={'extensions.zotero.useDataDir':True,'extensions.zotero.dataDir':str(data),'extensions.zotero.firstRun.skip':True,'extensions.zotero.httpServer.enabled':True,'extensions.zotero.httpServer.port':port,'extensions.zotero.httpServer.localAPI.enabled':True,'extensions.autoDisableScopes':0,'extensions.enabledScopes':15,'extensions.update.enabled':False}
    (profile/'user.js').write_text(''.join(f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k,v in prefs.items()))
    helper={'manifest_version':2,'name':'Isolated Reading List test','version':'0.0.1','applications':{'zotero':{'id':'litradar-test@litradar.local','update_url':'https://litradar.invalid/updates.json','strict_min_version':'10.0','strict_max_version':'10.0.*'}}}
    xpi=REPO/'zotero-litradar/litradar-pdf-bridge-0.2.0.xpi'
    code=(Path(__file__).with_name('native-reading-probe.js')).read_text().replace('TEST_ROOT',json.dumps(str(root))).replace('TEST_DATA_DIR',json.dumps(str(data))).replace('TEST_XPIS',json.dumps([str(Path(args.reading_list).resolve()),str(xpi)]))
    with zipfile.ZipFile(profile/'extensions/litradar-test@litradar.local.xpi','w') as z:
        z.writestr('manifest.json',json.dumps(helper));z.writestr('bootstrap.js',code)
    report={'testRoot':str(root)}
    print('Isolated native test:',root,flush=True)
    with (root/'native.log').open('w') as log:
        process=subprocess.Popen([args.zotero,'-no-remote','-profile',str(profile),'-headless'],stdout=log,stderr=log)
        try:
            report.update(wait_json(root/'ready.json',process))
            assert report['isActive'] and report['isCompatible'] and report['readingEndpoint']
            api=LocalAPI(Config({'zotero':{'api':f'http://127.0.0.1:{port}'}},root));api.auth_path=root/'auth.json'
            def command(value):
                result=root/'command-result.json'
                if result.exists():result.unlink()
                (root/'command.json').write_text(json.dumps(value))
                return wait_json(result,process,10)
            paper=Article(title='Synthetic Reading List Integration Test',journal='Test',url='https://example.invalid/test',doi='10.0000/litradar-native-test')
            row=ensure_item(api,paper,collection_date='2026-11-04')
            collections=api.get('/api/users/0/collections')
            archive_root=next(r for r in collections if r['data']['name']=='Litradar 文献雷达')
            archive_day=next(r for r in collections if r['data']['name']=='2026-11-04' and r['data']['parentCollection']==archive_root['key'])
            assert archive_day['key'] in row['data']['collections']
            ensure_item(api,paper,collection_date='2026-11-03')
            collections=api.get('/api/users/0/collections')
            previous_day=next(r for r in collections if r['data']['name']=='2026-11-03')
            again=ensure_item(api,paper,collection_date='2026-11-04')
            assert set(again['data']['collections'])=={archive_day['key'],previous_day['key']}
            assert len(api.get('/api/users/0/collections'))==3
            report['dailyArchiveAndIdempotency']=True
            row=again
            api.write('/api/users/0/items/'+row['key'],{'extra':'Project memo: preserve me','tags':[{'tag':'project Read analysis'}]},version=row['version'])
            view=set_status(api,paper,'To Read');key=view['key']
            assert command({'action':'inspect','key':key})['status']=='To Read'
            report['engineToReadingList']=True
            command({'action':'set','key':key,'status':'In Progress'})
            assert next(r for r in reading_list_payload(api)['items'] if r['key']==key)['status']=='In Progress'
            report['readingListToEngine']=True
            command({'action':'custom'})
            assert any(s['value']=='精读候选' for s in reading_list_payload(api)['statuses'])
            assert set_status(api,paper,'精读候选')['status']=='精读候选'
            report['customStatuses']=True
            stale=api.get('/api/users/0/items/'+key)
            set_status(api,paper,'Read')
            try:api.write('/api/users/0/litradar/reading',{'key':key,'status':'To Read','version':stale['version']},method='POST')
            except ZoteroUnavailable as error:assert '修改' in str(error)
            else:raise AssertionError('Stale write was not rejected')
            report['staleWriteRejected']=True
            current=api.get('/api/users/0/items/'+key)
            api.write('/api/users/0/items/'+key,{'tags':[{'tag':TODO_TAG},{'tag':'project Read analysis'}]},version=current['version'])
            migration=migrate_legacy(api,'To Read','Read')
            assert migration['preserved']==1 and not migration['failed']
            assert lookup(api,paper)['status']=='Read'
            current=api.get('/api/users/0/items/'+key)
            assert TODO_TAG not in {t['tag'] for t in current['data']['tags']}
            assert 'Project memo: preserve me' in current['data']['extra']
            assert 'project Read analysis' in {t['tag'] for t in current['data']['tags']}
            report['migrationPreservesExistingStatusAndMetadata']=True
            set_status(api,paper,'')
            current=api.get('/api/users/0/items/'+key)
            assert 'Read_Status:' not in current['data']['extra'] and 'Project memo: preserve me' in current['data']['extra']
            assert command({'action':'inspect','key':key})['status']==''
            report['clearStatus']=True
            print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
            (REPO/'zotero-litradar/verification-reading-list-0.2.0.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()

if __name__=='__main__':main()
