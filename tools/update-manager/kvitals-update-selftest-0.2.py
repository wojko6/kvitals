import importlib.util, io, json, pathlib, shutil, tempfile, unittest, zipfile, tarfile
from unittest.mock import patch
HERE=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('updater', HERE/'kvitals-update-0.2.py')
u=importlib.util.module_from_spec(spec); spec.loader.exec_module(u)
# A minimal synthetic gettext catalog; no runtime KDE dependency.
import struct

def mo_bytes():
    entries=[(b'', b'Content-Type: text/plain; charset=UTF-8\n'),(b'Memory','Pamięć'.encode()),(b'Processor',b'Procesor')]
    n=len(entries); start=28+16*n; a=b''; b=b''; ai=[]; bi=[]
    for k,v in entries:
        ai.append((len(k),start+len(a))); a+=k+b'\0'
    for k,v in entries:
        bi.append((len(v),start+len(a)+len(b))); b+=v+b'\0'
    return struct.pack('<7I',0x950412de,0,n,28,28+8*n,0,0)+b''.join(struct.pack('<2I',*x) for x in ai+bi)+a+b

def fixture(tag, extra=None):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('metadata.json',json.dumps({'KPlugin':{'Id':u.PLUGIN,'Version':'.'.join(map(str,u.version(tag)[:3])),'Description[pl]':'Opis'}}))
        z.writestr('contents/ui/main.qml','// '+tag)
        z.writestr(u.MO,mo_bytes())
        if extra: z.writestr(extra,'evil')
    payload=data.getvalue(); name=f'{u.PLUGIN}-{tag}.plasmoid'; files={name:payload,'SHA256SUMS':f'{u.digest(payload)}  {name}\n'.encode()}
    info={'tag_name':tag,'assets':[{'name':n,'browser_download_url':f'https://github.com/{u.REPO}/releases/download/{tag}/{n}','state':'uploaded','size':len(d),'digest':'sha256:'+u.digest(d)} for n,d in files.items()]}
    return info, lambda url,limit: files[url.rsplit('/',1)[1]], payload

class Tests(unittest.TestCase):
    def test_version(self):
        self.assertGreater(u.version('v3.4.0-pl.10'),u.version('v3.4.0-pl.2'))
        self.assertGreater(u.version('v3.5.0-pl.1'),u.version('v3.4.0-pl.10'))
        for t in ['v3.4.0','v3.4.0-pl.0','../x','v3.4.0-pl.2-rc']:
            with self.assertRaises(RuntimeError): u.version(t)
    def test_package(self):
        info,fetch,data=fixture('v3.4.0-pl.3')
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(u.package(info,pathlib.Path(t),fetch),u.digest(data))
            self.assertEqual(len(u.inventory(pathlib.Path(t)/'candidate')),3)
    def test_bad_digest(self):
        info,fetch,data=fixture('v3.4.0-pl.3'); info['assets'][0]['digest']='sha256:'+'0'*64
        with tempfile.TemporaryDirectory() as t,self.assertRaisesRegex(RuntimeError,'integrity'):
            u.package(info,pathlib.Path(t),fetch)
    def test_bad_manifest(self):
        info,fetch,data=fixture('v3.4.0-pl.3'); bad=b'wrong'; info['assets'][1]['digest']='sha256:'+u.digest(bad); info['assets'][1]['size']=len(bad)
        with tempfile.TemporaryDirectory() as t,self.assertRaisesRegex(RuntimeError,'SHA256SUMS'):
            u.package(info,pathlib.Path(t),lambda url,limit: bad if url.endswith('SHA256SUMS') else data)
    def test_traversal(self):
        _,_,data=fixture('v3.4.0-pl.3','../bad')
        with tempfile.TemporaryDirectory() as t,self.assertRaisesRegex(RuntimeError,'Unsafe'):
            u.unpack(data,'v3.4.0-pl.3',pathlib.Path(t))
    def test_registry(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t)/'registry'; p.write_text('<root><stuff><id>2347917</id></stuff></root>')
            with self.assertRaisesRegex(RuntimeError,'KDE Store'): u.registry_clear(p)
            p.write_text('<root><stuff><id>other</id></stuff></root>');u.registry_clear(p)
    def test_adopt_check_stage_and_drift(self):
        initial, fetch1,data1=fixture(u.INITIAL); later, fetch2,data2=fixture('v3.4.0-pl.3')
        with tempfile.TemporaryDirectory() as t:
            t=pathlib.Path(t); w=t/'widget'; base=t/'base'; reg=t/'reg'
            u.unpack(data1,u.INITIAL,w)
            def package_proxy(info,dest): return real_package(info,dest,fetch1 if info is initial else fetch2)
            real_package=u.package
            with patch.object(u,'INITIAL_DIGEST',u.digest(data1)),patch.object(u,'release',side_effect=lambda tag=None: initial if tag else later),patch.object(u,'package',side_effect=package_proxy):
                u.run('adopt',base,w,reg); baseline=u.inventory(w); u.run('check',base,w,reg)
                self.assertEqual(u.inventory(w),baseline); self.assertEqual(json.loads((base/'staged/pending.json').read_text())['tag'],'v3.4.0-pl.3')
                with self.assertRaisesRegex(RuntimeError,'Already adopted'): u.run('adopt',base,w,reg)
                (w/'contents/ui/main.qml').write_text('changed')
                with self.assertRaisesRegex(RuntimeError,'Local files changed'): u.run('status',base,w,reg)
    def test_same_and_older(self):
        info,_,data=fixture(u.INITIAL)
        with tempfile.TemporaryDirectory() as t:
            t=pathlib.Path(t); w=t/'widget'; base=t/'base';base.mkdir();u.unpack(data,u.INITIAL,w)
            u.atomic_json(base/'state.json',{'installed':u.INITIAL,'files':u.inventory(w),'package_sha256':u.digest(data)})
            with patch.object(u,'release',return_value=info):u.run('check',base,w,t/'reg')
            older,_,_=fixture('v3.4.0-pl.1')
            with patch.object(u,'release',return_value=older):u.run('check',base,w,t/'reg')
            self.assertFalse((base/'staged').exists())
            info['assets'][0]['digest']='sha256:'+'0'*64
            with patch.object(u,'release',return_value=info),self.assertRaisesRegex(RuntimeError,'Published asset changed'):u.run('check',base,w,t/'reg')
    def setup_deployment(self, t):
        t=pathlib.Path(t); w=t/'widget'; base=t/'base'; base.mkdir(); reg=t/'reg'
        info1, fetch1, data1=fixture(u.INITIAL)
        info2, fetch2, data2=fixture('v3.4.0-pl.3')
        u.unpack(data1,u.INITIAL,w)
        baseline=u.inventory(w)
        u.atomic_json(base/'state.json',{'installed':u.INITIAL,'files':baseline,'package_sha256':u.digest(data1)})
        with patch.object(u,'release',return_value=info2),patch.object(u,'package',side_effect=lambda info,dest: real_package(info,dest,fetch2)):
            u.run('check',base,w,reg)
        return w,base,reg,info2,baseline
    def test_apply_rollback_history(self):
        with tempfile.TemporaryDirectory() as t:
            w,base,reg,info,baseline=self.setup_deployment(t)
            with patch.object(u,'release',return_value=info),patch.object(u,'plasma_stopped'):
                u.run('apply',base,w,reg)
                current=json.loads((base/'state.json').read_bytes())
                self.assertEqual(current['installed'],'v3.4.0-pl.3')
                self.assertEqual(u.inventory(w),current['files'])
                self.assertEqual(u.inventory(base/current['backup']),baseline)
                self.assertFalse((base/'transaction.json').exists())
                self.assertFalse((base/'staged/pending.json').exists())
                u.run('rollback',base,w,reg)
                restored=json.loads((base/'state.json').read_bytes())
                self.assertEqual(restored['installed'],u.INITIAL)
                self.assertEqual(u.inventory(w),baseline)
                self.assertEqual([x['action'] for x in restored['history']],['apply','rollback'])
                u.run('history',base,w,reg)
    def test_active_plasma_blocks(self):
        with tempfile.TemporaryDirectory() as t:
            w,base,reg,info,baseline=self.setup_deployment(t)
            with patch.object(u,'plasma_stopped',side_effect=RuntimeError('active')),self.assertRaisesRegex(RuntimeError,'active'):
                u.run('apply',base,w,reg)
            self.assertEqual(u.inventory(w),baseline)
    def test_tampered_staging(self):
        with tempfile.TemporaryDirectory() as t:
            w,base,reg,info,baseline=self.setup_deployment(t)
            item=json.loads((base/'staged/pending.json').read_bytes())
            (base/'staged'/item['asset']).write_bytes(b'bad')
            with patch.object(u,'plasma_stopped'),patch.object(u,'release',return_value=info),self.assertRaisesRegex(RuntimeError,'Staged asset integrity'):
                u.run('apply',base,w,reg)
            self.assertEqual(u.inventory(w),baseline)
    def test_changed_remote_release(self):
        with tempfile.TemporaryDirectory() as t:
            w,base,reg,info,baseline=self.setup_deployment(t)
            info['assets'][0]['digest']='sha256:'+'0'*64
            with patch.object(u,'plasma_stopped'),patch.object(u,'release',return_value=info),self.assertRaisesRegex(RuntimeError,'digest changed'):
                u.run('apply',base,w,reg)
            self.assertEqual(u.inventory(w),baseline)
    def test_recovery_after_exchange(self):
        with tempfile.TemporaryDirectory() as t:
            w,base,reg,info,baseline=self.setup_deployment(t)
            exchange=u.exchange
            def interrupt(a,b):
                exchange(a,b)
                raise RuntimeError('simulated interruption')
            with patch.object(u,'plasma_stopped'),patch.object(u,'release',return_value=info),patch.object(u,'exchange',side_effect=interrupt),self.assertRaisesRegex(RuntimeError,'interruption'):
                u.run('apply',base,w,reg)
            self.assertTrue((base/'transaction.json').exists())
            u.run('status',base,w,reg)
            self.assertEqual(json.loads((base/'state.json').read_bytes())['installed'],'v3.4.0-pl.3')
            self.assertFalse((base/'transaction.json').exists())
    def test_recovery_before_exchange(self):
        with tempfile.TemporaryDirectory() as t:
            w,base,reg,info,baseline=self.setup_deployment(t)
            with patch.object(u,'plasma_stopped'),patch.object(u,'release',return_value=info),patch.object(u,'exchange',side_effect=RuntimeError('simulated interruption')),self.assertRaisesRegex(RuntimeError,'interruption'):
                u.run('apply',base,w,reg)
            self.assertTrue((base/'transaction.json').exists())
            u.run('status',base,w,reg)
            self.assertEqual(u.inventory(w),baseline)
            self.assertFalse((base/'transaction.json').exists())
    def test_corrupt_rollback(self):
        with tempfile.TemporaryDirectory() as t:
            w,base,reg,info,baseline=self.setup_deployment(t)
            with patch.object(u,'plasma_stopped'),patch.object(u,'release',return_value=info):
                u.run('apply',base,w,reg)
                current=json.loads((base/'state.json').read_bytes())
                (base/current['backup']/'contents/ui/main.qml').write_text('tampered')
                with self.assertRaisesRegex(RuntimeError,'Rollback backup integrity'):
                    u.run('rollback',base,w,reg)
                self.assertEqual(u.inventory(w),current['files'])
real_package=u.package
if __name__=='__main__':unittest.main(verbosity=2)
