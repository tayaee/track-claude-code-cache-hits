# Track Claude Code Cache Status (hits/misses)

## How to run
* Install uv
* Run: `uvx --isolated --python 3.12 --from git+https://github.com/tayaee/track-claude-code-cache-hits.git track_claude_code_cache_hits`

## Example
```
c:\temp>uvx --isolated --python 3.12 --from git+https://github.com/tayaee/track-claude-code-cache-hits.git track_claude_code_cache_hits
Installed 1 package in 147ms
Monitoring cache logs in C:\Users\user1\.claude\projects...
-------------------------------------------------------------------------------------------------------------------
[e--src-test5   ] HIT:   21,896 | MISS:       16 | Ratio: 100% | CUM-HIT:     21,896 | CUM-MISS:         16 | CUM-Ratio: 100%
[e--src-test5   ] HIT:   21,912 | MISS:       13 | Ratio: 100% | CUM-HIT:     43,808 | CUM-MISS:         29 | CUM-Ratio: 100%
[e--src-test6   ] HIT:   11,382 | MISS:    8,099 | Ratio:  58% | CUM-HIT:     55,190 | CUM-MISS:      8,128 | CUM-Ratio:  87%
[e--src-test6   ] HIT:   11,382 | MISS:    8,099 | Ratio:  58% | CUM-HIT:     66,572 | CUM-MISS:     16,227 | CUM-Ratio:  80%
...
```
