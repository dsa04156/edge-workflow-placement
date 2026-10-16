"""Seeded sweeps of fixed edge input conditions and distinct selected placements."""


def schedule(groups,warmups,measured,seed,assigned,regime):
    rows=[]
    keys=sorted({g['image_id'] for g in groups})
    for sweep in range(warmups+measured):
        ordered=assigned([{'image_id':key} for key in keys],seed+sweep*10)
        for index,item in enumerate(ordered):
            key=item['image_id']
            plans=assigned([g for g in groups if g['image_id']==key],seed+sweep*10+index+1)
            for position,plan in enumerate(plans):
                rows.append({**plan,'id':f'{regime}-{key}-s{sweep}-p{position}',
                             'sweep':sweep,'position':position,'warmup':sweep<warmups,'regime':regime})
    return rows
