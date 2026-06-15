#!/bin/sh
for candidate in /usr/bin/python3 /usr/local/bin/python3 /usr/bin/python /usr/local/bin/python; do
    if [ -x "$candidate" ]; then
        exec "$candidate" "$0.py" "$@"
    fi
done

printf 'Status: 500 Internal Server Error\r\n'
printf 'Content-Type: text/plain\r\n\r\n'
printf 'Delta Coding could not find a Python interpreter for index.cgi.\n'
exit 0

