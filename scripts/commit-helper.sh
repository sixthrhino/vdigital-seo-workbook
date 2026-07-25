#!/bin/bash

# Commit message helper for conventional commits
# Usage: ./scripts/commit-helper.sh <type> "commit message"

if [ $# -lt 2 ]; then
    echo "Usage: $0 <type> \"commit message\""
    echo ""
    echo "Types: feat, fix, test, docs, chore, refactor, perf, infra, ci, style"
    echo ""
    echo "Examples:"
    echo "  $0 feat \"add new user authentication\""
    echo "  $0 fix \"correct database connection timeout\""
    echo "  $0 infra \"update terraform configuration\""
    exit 1
fi

TYPE=$1
MESSAGE=$2

# Validate type
VALID_TYPES="feat fix test docs chore refactor perf infra ci style"
if [[ ! " $VALID_TYPES " =~ " $TYPE " ]]; then
    echo "❌ Invalid type: $TYPE"
    echo "Valid types: $VALID_TYPES"
    exit 1
fi

# Create commit message
COMMIT_MSG="$TYPE: $MESSAGE"

echo "Committing with message: '$COMMIT_MSG'"
git commit -m "$COMMIT_MSG"
