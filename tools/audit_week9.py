#!/usr/bin/env python3
"""Read-only audit of week-9 copied/graph-only schedule objects. Never writes database."""
import argparse,collections,gzip,json,pathlib
p=argparse.ArgumentParser();p.add_argument('export');p.add_argument('--week',type=int,default=9);p.add_argument('--output',default='week9_audit.json');args=p.parse_args()
with (gzip.open(args.export,'rt',encoding='utf-8') if args.export.endswith('.gz') else open(args.export,encoding='utf-8')) as f: data=json.load(f)
s=data['schedule']; ass=s.get('assignment') or {}; up=set(map(str,s.get('unplaced') or [])); week=args.week
# Restrict audit to explicitly week-scoped occurrences; don't infer recurring pattern.
rows=[x for x in s['instances'] if week in (x.get('customWeeks') or []) and x.get('weekPattern')=='custom']
def signature(x):
    parts=[(str(x.get('groupId','')),str(x.get('loadId','')),int(x.get('subgroup') or 0))]
    parts += [(str(t.get('groupId','')),str(t.get('loadId','')),int(t.get('subgroup') or 0)) for t in (x.get('streamParticipants') or [])]
    # Requirement layer and precise participant loadIds keep 0.5 pair and streams separate.
    return (tuple(sorted(parts)),str(x.get('requirementLayer') or ('H' if x.get('halfPair') else '1')),
            bool(x.get('halfPair')),str(x.get('format') or 'inperson'),str(x.get('teacherId') or ''),str(x.get('subjectId') or ''),str(x.get('typeId') or ''))
groups=collections.defaultdict(list)
for x in rows: groups[signature(x)].append(x)
flags={'graph_only':0,'exact_copy':0,'placed':0,'unplaced':0}
for x in rows:
    k=str(x.get('instId'))
    flags['graph_only']+=bool(x.get('bulkWeekGraphOnly'))
    flags['exact_copy']+=bool(x.get('exactBulkWeekCopy'))
    flags['placed']+=k in ass
    flags['unplaced']+=k in up
# Report only. No proposed deletion unless candidate is marked technical + duplicate semantic demand.
candidates=[]; ambiguous=[]
for sig,items in groups.items():
    if len(items)<2:continue
    placed=[x for x in items if str(x.get('instId')) in ass]
    technical=[x for x in items if x.get('bulkWeekGraphOnly') or x.get('exactBulkWeekCopy')]
    entry={'participants':sig[0], 'layer':sig[1],'halfPair':sig[2], 'format':sig[3],
           'teacherId':sig[4], 'subjectId':sig[5], 'typeId':sig[6],
           'ids':[x.get('instId') for x in items], 'placedIds':[x.get('instId') for x in placed],
           'technicalIds':[x.get('instId') for x in technical]}
    # Manually approve even these: shared semantic key doesn't prove multiple required lessons are duplicates.
    (candidates if technical and placed else ambiguous).append(entry)
report={'week':week,'allExplicitWeekRows':len(rows),'flags':flags,
        'duplicatedSemanticGroups':len(candidates)+len(ambiguous),
        'potentiallyCoveredTechnicalGroups':candidates,'ambiguousGroups':ambiguous,
        'note':'READ-ONLY. No records removed. Review layers, intended weeklyPairs, flows and placements before edits.'}
pathlib.Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if not isinstance(v,list)},ensure_ascii=False,indent=2))
print('potentially covered:',len(candidates),'ambiguous:',len(ambiguous),'report:',args.output)
