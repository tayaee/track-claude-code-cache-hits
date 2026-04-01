# Claude Code Cache Tail

**Requirement:** Install `uv`

## Usage

```sh
uvx -q ccctail -h                              # help
uvx -q ccctail -f                              # tail mode
uvx -q ccctail --all --format=csv > out.csv    # Excel analysis
uvx -q ccctail --all --format=json > out.jsonl # JQ analysis
```

## From source

```sh
git clone https://github.com/tayaee/ccctail.git
cd ccctail && uv -q run ccctail -h
```
