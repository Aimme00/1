#!/bin/zsh

set -e

PROJECT_DIR="${0:A:h}"
PREVIEW_FILE="$PROJECT_DIR/web/index.html"

if [[ ! -f "$PREVIEW_FILE" ]]; then
  osascript -e 'display alert "AskData 页面不存在" message "请确认项目文件完整后再试。" as critical'
  exit 1
fi

open "$PREVIEW_FILE"

