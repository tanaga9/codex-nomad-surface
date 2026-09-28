---
title: Quick Prompt
description: Short mobile requests
inputs:
  action:
    label: Action
    type: select
    default: "summarize"
    options: ["summarize", "review", "implement"]
  target:
    label: Target
    type: text
  detail:
    label: Extra detail
    type: text
    required: false
    default: ""
---
Please {{input.action}} {{input.target}}.

{{input.detail}}
