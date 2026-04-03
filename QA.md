uv tool install . -e --force

-f
    ccctail -f

-n, --lines
    ccctail
    ccctail -n=3

--all, --format
    ccctail --all | head -25
    ccctail --all --format=csv | head -25
    ccctail --all --format=json | head -25

-f and --format    
    ccctail -f --format=csv
    ccctail -f --format=json
    