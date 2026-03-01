#!/bin/bash
# repo-pilot — AI-powered repo assistant
# Install: cp run.sh ~/.local/bin/repo-pilot && chmod +x ~/.local/bin/repo-pilot
#
# Usage:
#   repo-pilot bake ~/git/myrepo                    → repo-pilot:myrepo
#   repo-pilot bake ~/git/myrepo -a ~/git/dep       → repo-pilot:myrepo-also-dep
#   repo-pilot ask "How do I build?" -r ~/git/myrepo → runs in container
#   repo-pilot scan ~/git/myrepo                     → runs in container
#   repo-pilot -r ~/git/myrepo                       → interactive mode

set -eo pipefail

RUNTIME="${REPO_PILOT_RUNTIME:-podman}"
BASE_IMAGE="${REPO_PILOT_BASE_IMAGE:-repo-pilot:latest}"
API_KEY="${REPO_PILOT_LLM_API_KEY:-${ANTHROPIC_API_KEY:-}}"

# --- bake subcommand: orchestrates containers on the host ---
do_bake() {
    local repos=()
    local image=""
    local primary=""

    # Parse args
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -i|--image)  image="$2"; shift 2 ;;
            -a|--also)   repos+=("$2"); shift 2 ;;
            --base)      BASE_IMAGE="$2"; shift 2 ;;
            --runtime)   RUNTIME="$2"; shift 2 ;;
            -*)          echo "Unknown option: $1"; exit 1 ;;
            *)
                if [[ -z "$primary" ]]; then
                    primary="$1"
                else
                    repos+=("$1")
                fi
                shift
                ;;
        esac
    done

    if [[ -z "$primary" ]]; then
        echo "Usage: repo-pilot bake <repo-path> [--also <repo>...] [--image <name>]"
        exit 1
    fi

    # Resolve primary repo
    primary="$(cd "$primary" && pwd)"
    local primary_name
    primary_name="$(basename "$primary")"

    # Build default image tag
    if [[ -z "$image" ]]; then
        image="repo-pilot:${primary_name}"
        if [[ ${#repos[@]} -gt 0 ]]; then
            for r in "${repos[@]}"; do
                image="${image}-also-$(basename "$r")"
            done
        fi
    fi

    local container_name="repo-pilot-bake-${primary_name}-$$"

    # Build volume mounts and repo paths inside container
    local volumes=("-v" "${primary}:/repos/${primary_name}:ro")
    local internal_paths=("/repos/${primary_name}")

    for r in "${repos[@]}"; do
        r="$(cd "$r" && pwd)"
        local rname
        rname="$(basename "$r")"
        volumes+=("-v" "${r}:/repos/${rname}:ro")
        internal_paths+=("/repos/${rname}")
    done

    echo "==> Step 1/2: Indexing repos in container..."
    echo "    Primary: ${primary_name}"
    for r in "${repos[@]}"; do
        echo "    Also:    $(basename "$r")"
    done

    $RUNTIME run --name "$container_name" \
        "${volumes[@]}" \
        "$BASE_IMAGE" \
        _bake_internal "${internal_paths[@]}"

    echo "==> Step 2/2: Committing image ${image}..."
    $RUNTIME commit \
        --change 'ENV REPO_PILOT_BAKED="/baked"' \
        "$container_name" "$image"

    $RUNTIME rm -f "$container_name" > /dev/null 2>&1

    echo ""
    echo "Baked image ready: ${image}"
    echo ""
    echo "Usage:"
    echo "  ${RUNTIME} run --rm -it \\"
    echo "    -e REPO_PILOT_LLM_API_KEY=\$ANTHROPIC_API_KEY \\"
    echo "    ${image} \\"
    echo "    ask \"How do I install this?\""
}

# --- default: pass-through to container ---
do_run() {
    # Collect -v mounts, -r/--repo, -a/--also flags to auto-mount repos
    local volumes=()
    local args=()
    local need_tty="-it"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -v|--volume)
                volumes+=("-v" "$2")
                shift 2
                ;;
            -r|--repo)
                local rpath
                rpath="$(cd "$2" && pwd)"
                local rname
                rname="$(basename "$rpath")"
                volumes+=("-v" "${rpath}:/repos/${rname}:ro")
                args+=("$1" "/repos/${rname}")
                shift 2
                ;;
            --repo=*)
                local rpath
                rpath="$(cd "${1#*=}" && pwd)"
                local rname
                rname="$(basename "$rpath")"
                volumes+=("-v" "${rpath}:/repos/${rname}:ro")
                args+=("--repo" "/repos/${rname}")
                shift
                ;;
            -a|--also)
                local rpath
                rpath="$(cd "$2" && pwd)"
                local rname
                rname="$(basename "$rpath")"
                volumes+=("-v" "${rpath}:/repos/${rname}:ro")
                args+=("$1" "/repos/${rname}")
                shift 2
                ;;
            scan|index)
                # These take a positional repo path — next arg is the path
                args+=("$1")
                if [[ $# -gt 1 && ! "$2" =~ ^- ]]; then
                    local rpath
                    rpath="$(cd "$2" && pwd)"
                    local rname
                    rname="$(basename "$rpath")"
                    volumes+=("-v" "${rpath}:/repos/${rname}:ro")
                    args+=("/repos/${rname}")
                    shift
                fi
                shift
                ;;
            *)
                args+=("$1")
                shift
                ;;
        esac
    done

    local env_args=()
    if [[ -n "$API_KEY" ]]; then
        env_args+=("-e" "REPO_PILOT_LLM_API_KEY=${API_KEY}")
    fi
    # Pass through any REPO_PILOT_ env vars
    while IFS= read -r var; do
        [[ -n "$var" ]] && env_args+=("-e" "$var")
    done < <(env | grep '^REPO_PILOT_' | grep -v 'RUNTIME\|BASE_IMAGE')

    exec $RUNTIME run --rm $need_tty \
        "${volumes[@]}" \
        "${env_args[@]}" \
        "$BASE_IMAGE" \
        "${args[@]}"
}

# --- main ---
case "${1:-}" in
    bake)
        shift
        do_bake "$@"
        ;;
    *)
        do_run "$@"
        ;;
esac
