# Reading the monograph cheaply

`docs/tycho-monograf-v1.3.2.html` is 360 KB of Turkish HTML. Never load it whole.

- Find a section: `grep -n '<phrase>' docs/tycho-monograf-v1.3.2.html`, then read only those lines. Every issue lists its anchors and search phrases.
- Dump the canonical registry once per session (`runs/` is gitignored):

```bash
uv run python -c "import re,html,json;s=open('docs/tycho-monograf-v1.3.2.html',encoding='utf-8').read();m=re.search(r'id=\"tycho-registry\"[^>]*>(.*?)</pre>',s,re.S);json.dump(json.loads(html.unescape(re.sub(r'<[^>]+>','',m.group(1)))),open('runs/registry.json','w'),ensure_ascii=False,indent=1)"
```

- Map of sections → code: `PLAN.md` §3. Most used: §07 `#matematik` (accounting), §08 `#mimari` (core rule), §08A `#expert` (contracts), §09 `#bilesenler` (C-01…C-10), §10 `#altyapi` (scanner, Nautilus), §11 `#dogrulama` (gates, nulls, parity), §14 `#e001` (the experiment), §16 `#acik` (PENDING list).
- Registry paths used most: `first_experiment`, `market_state_architecture`, `accounting_ledger`, `scientific_judge`, `decisions` (D-04, D-06, D-22, D-24).
- §19–19G (Hermes, Telegram, agent organisation) is out of scope.
