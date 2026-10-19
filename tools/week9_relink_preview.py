#!/usr/bin/env python3
"""Read-only inspection of possible cross-ID links between restored week 7 and week 9."""
import argparse, collections, gzip, json
from pathlib import Path

def identity(r):
    members=[(str(r.get('groupId','')),int(r.get('subgroup') or 0))]
    members += [(str(p.get('groupId','')),int(p.get('subgroup') or 0)) for p in r.get('streamParticipants',[])]
    return (tuple(sorted(members)),str(r.get('subjectId','')),str(r.get('teacherId','')),
            str(r.get('typeId','')),str(r.get('format','inperson')),bool(r.get('halfPair')),
            str(r.get('requirementLayer','1')))

def main():
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('--output',default='week9_relink_preview.json');args=p.parse_args()
    opener=gzip.open if str(args.source).endswith('.gz') else open
    with opener(args.source,'rt',encoding='utf-8') as f: data=json.load(f)
    s=data['schedule'];a=s['assignment']; graph=[r for r in s['instances'] if r.get('bulkWeekGraphOnly') and r.get('customWeeks')==[9]]
    source=[r for r in s['instances'] if 7 in r.get('customWeeks',[]) and r.get('instId') in a]
    indexed=collections.defaultdict(list)
    for r in graph:indexed[identity(r)].append(r)
    matches=[];ambiguous=[]
    for r in source:
        candidates=indexed.get(identity(r),[])
        if len(candidates)==1:
            matches.append({'source_7':r['instId'],'target_9_unplaced':candidates[0]['instId'],
                'source_load':r.get('loadId'),'target_load':candidates[0].get('loadId'),
                'source_position':a[r['instId']]})
        elif len(candidates)>1:ambiguous.append(r['instId'])
    # A matching source may map to the same target. Report these separately, do not write.
    counts=collections.Counter(m['target_9_unplaced'] for m in matches)
    safe=[m for m in matches if counts[m['target_9_unplaced']]==1]
    report={'mode':'READ_ONLY','source_week7_assigned':len(source),'week9_graph_only':len(graph),
      'strict_unique_candidate_pairs':len(matches),'unique_one_to_one_candidates':len(safe),
      'multiple_source_to_one_target':sum(v>1 for v in counts.values()),
      'ambiguous_source_ids':ambiguous[:500], 'candidates':safe,
      'warning':'These matches have NOT been conflict-checked or applied. Do not upload as a DB replacement.'}
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('candidates','ambiguous_source_ids')},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
