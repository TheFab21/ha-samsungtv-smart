"""Dump ArtAppTextResources of every locale of an org.tizen.art-app package to JSON.

Usage: python3 extract_resources.py <org.tizen.art-app/bin> <out.json>
Needs: pip install dnfile
"""
import sys, json, dnfile, os
out={}
base=sys.argv[1]
for loc in sorted(os.listdir(base)):
    f=os.path.join(base,loc,'ArtAppTextResources.resources.dll')
    if not os.path.isfile(f): continue
    pe=dnfile.dnPE(f); d={}
    for r in pe.net.resources:
        for e in r.data.entries:
            v=e.value if e.value is not None else e.data
            if isinstance(v,bytes): v=v.decode('utf-8','replace')
            d[str(e.name)]=v
    out[loc]=d
json.dump(out,open(sys.argv[2],'w'),ensure_ascii=False,indent=1,sort_keys=True)
print(len(out), {k:len(v) for k,v in list(out.items())[:5]})
