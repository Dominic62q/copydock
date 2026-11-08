#!/bin/sh
# usage: linux.sh <bundle dir> <installer args...>   runs install.sh inside Alpine Linux
dir=$1; shift
exec docker run --rm --network host -v /var/run/docker.sock:/var/run/docker.sock -v "$dir:$dir" -w "$dir" docker:cli sh ./install.sh "$@"
