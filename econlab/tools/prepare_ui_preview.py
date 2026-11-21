"""Create an isolated browser-test copy; never point the test server at the real DB."""
from pathlib import Path
import sqlite3,shutil,sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store,catalog,fitz
catalog.init();target=(store.ROOT/'.local'/'ui-preview').resolve()
if target==store.DATA or not target.is_relative_to(store.ROOT/'.local'):raise SystemExit('Refusing to replace the active library')
if target.exists():raise SystemExit('Preview already exists. Stop its server and remove the isolated ui-preview directory before rebuilding it.')
target.mkdir();(target/'.preview-only').write_text(str(store.DATA),encoding='utf-8')
with sqlite3.connect(store.DATA/'econlab.sqlite3') as source,sqlite3.connect(target/'econlab.sqlite3') as dest:
 source.backup(dest);dest.execute('UPDATE sessions SET thread_id=NULL');dest.commit()
with store.db() as c:assets=[dict(a) for a in c.execute('SELECT * FROM assets')];runs=[r[0] for r in c.execute('SELECT id FROM runs')]
for a in assets:
 dest=target/a['path'];dest.parent.mkdir(exist_ok=True,parents=True);shutil.copy2(catalog.asset_path(a['id']),dest)
for rid in runs:
 dest=target/'runs'/rid;dest.mkdir(exist_ok=True,parents=True)
 for name in ('report.json','solve-report.json','result.mat','stdout.log','stderr.log'):
  source=store.DATA/'runs'/rid/name
  if source.exists():shutil.copy2(source,dest/name)
d=fitz.open();page=d.new_page();page.insert_text((50,50),'A synthetic paper for interface testing.');d.save(target/'fixture.pdf');d.close();print(target)
