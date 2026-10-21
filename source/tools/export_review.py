"""Copy only complete, context-bound source-safe receipts; never load the app."""
import hashlib,json,re,sys
from pathlib import Path
from uuid import uuid4
from app.safe_receipts import prepare_review_safe,ReceiptError,_safe_path,_new_file
ROOT=Path(__file__).resolve().parents[1]

def collect_plans(root,expected):
    root=_safe_path(root,review=True)
    plans=[];total=0
    runs=list(root.iterdir())
    if not 1<=len(runs)<=64:raise ReceiptError('SIZE_LIMIT')
    for run in sorted(runs):
        _safe_path(run,review=True)
        if not run.is_dir() or re.fullmatch(r'run_[0-9a-f]{32}',run.name) is None:
            raise ReceiptError('UNAPPROVED_ARTIFACT')
        same_run=None;roles=0
        for role in ('launcher','worker'):
            source=_safe_path(run/role,review=True)
            if not source.is_dir():continue
            names=['identity.json','events.jsonl']
            for name in ('lifecycle.json','emergency.json'):
                if (source/name).is_file():names.append(name)
            context,prepared=prepare_review_safe(source,names)
            if context['run_alias']!=run.name or any(context[k]!=v for k,v in expected.items()):
                raise ReceiptError('CONTEXT_MISMATCH')
            if same_run is not None and context!=same_run:raise ReceiptError('CONTEXT_MISMATCH')
            same_run=context;roles+=1;plans.append((run.name,role,prepared))
            total+=sum(len(data) for name,data in prepared)
            if total>64*1024*1024:raise ReceiptError('SIZE_LIMIT')
        if not roles:raise ReceiptError('MISSING_FIELDS')
    return plans

def main():
    try:
        manifest_bytes=(ROOT/'PACKAGE_INTEGRITY.json').read_bytes();manifest=json.loads(manifest_bytes)
        expected={'build_id':manifest['build_id'],'source_sha256':manifest['source_aggregate_sha256'],
                  'package_sha256':hashlib.sha256(manifest_bytes).hexdigest().upper(),'case_id':'LIFECYCLE'}
        plans=collect_plans(ROOT/'.local/review_safe',expected)
        exports=_safe_path(ROOT/'.local/review_exports');exports.mkdir(exist_ok=True)
        destination=exports/('export_'+uuid4().hex);destination.mkdir(exist_ok=False)
        rows=[]
        for run,role,prepared in plans:
            target=destination/run/role;target.mkdir(parents=True,exist_ok=False)
            files=[]
            for name,data in prepared:
                _new_file(target/name,data)
                files.append({'artifact':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest().upper()})
            rows.append({'run_alias':run,'role':role,'files':files})
        _new_file(destination/'EXPORT_INDEX.json',(json.dumps({'scope':'SOURCE_SAFE_ONLY','files':rows,'private_files_read':False},indent=2)+'\n').encode('ascii'))
        print('Review-safe export complete under .local/review_exports/'+destination.name)
        return 0
    except ReceiptError as error:
        print('REVIEW_EXPORT_BLOCKED_'+error.code);return 1
    except (OSError,ValueError,KeyError):
        print('REVIEW_EXPORT_BLOCKED_METADATA_OR_IO');return 1
if __name__=='__main__':sys.exit(main())
