from pathlib import Path

import pytest

from codex_nomad_surface.prompt_templates import TemplateError, load_templates, parse_template


def test_literal_body_and_line_endings_are_preserved(tmp_path):
    root = tmp_path / 'ops/prompts'
    root.mkdir(parents=True)
    source = '  Text {literal} {{other.value}}\r\n```json\r\n{"x": 1}\r\n```\r\n'
    (root / 'literal.md').write_bytes(source.encode())
    templates, errors = load_templates(str(tmp_path))
    assert not errors
    assert templates[0].expand({}) == source
    (root / 'literal.md').write_text('Updated\n')
    assert load_templates(str(tmp_path))[0][0].body == 'Updated\n'


def test_unique_markers_code_blocks_escape_and_no_recursive_expansion():
    source = '  {{input.target}}\n```\n{{input.target}}\n```\n\\{{input.literal}}\n'
    template = parse_template(source, 'x.md')
    assert [i.name for i in template.inputs] == ['target']
    value = '{{input.nested}} </script> $HOME'
    assert template.expand({'target': value}) == f'  {value}\n```\n{value}\n```\n{{{{input.literal}}}}\n'
    with pytest.raises(TemplateError, match='Please fill'):
        template.expand({})


def test_front_matter_defaults_and_optional_select():
    template = parse_template('''---
title: Review
inputs:
  mode:
    type: select
    options: [Tests, Correctness]
    default: Tests
  notes:
    type: textarea
    required: false
---
{{input.mode}}\n{{input.notes}}\n''', 'x.md')
    assert template.expand({}) == 'Tests\n\n'
    assert template.expand({'mode': 'Correctness', 'notes': 'one\ntwo'}) == 'Correctness\none\ntwo\n'
    with pytest.raises(TemplateError, match='available option'):
        template.expand({'mode': 'Other'})


@pytest.mark.parametrize('source', [
    '{{input.bad-name}}', '{{input.}}', '{{ input.name }}', '{{input.name',
    '---\ntitle: X\n', '---\ntitle: X\ntitle: Y\n---\nBody',
    '---\ninputs: [x]\n---\nBody',
    '---\ninputs:\n  unused: {}\n---\nBody',
    '---\ninputs:\n  x:\n    type: command\n---\n{{input.x}}',
    '---\ninputs:\n  x:\n    default: 42\n---\n{{input.x}}',
    '---\ninputs:\n  x:\n    type: select\n    options: [A, B]\n    default: C\n---\n{{input.x}}',
    '---\ninputs:\n  x:\n    required: "false"\n---\n{{input.x}}',
    '---\ntitle: !!python/object:builtins.str {}\n---\nBody',
])
def test_invalid_templates_fail_visibly(source):
    with pytest.raises(TemplateError):
        parse_template(source, 'bad.md')


def test_bad_files_do_not_hide_good_files_or_cross_project_boundaries(tmp_path):
    project = tmp_path / 'project'
    root = project / 'ops/prompts'
    root.mkdir(parents=True)
    (root / 'good.md').write_text('Good')
    (root / 'bad.md').write_text('{{input.}}')
    (root / 'link.md').symlink_to(root / 'good.md')
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'external.md').write_text('External')
    (root / 'linked').symlink_to(other, target_is_directory=True)
    templates, errors = load_templates(str(project))
    assert [t.id for t in templates] == ['good.md']
    assert len(errors) == 1 and 'bad.md' in errors[0]
    assert load_templates(str(other)) == ([], [])
    assert load_templates('') == ([], [])


def test_bundled_templates_are_valid():
    templates, errors = load_templates(str(Path(__file__).resolve().parents[1]))
    assert not errors
    assert {t.id for t in templates} >= {'project_work.md', 'quick_prompt.md'}


def test_append_script_cannot_be_terminated_by_prompt_text():
    from codex_nomad_surface.app import append_once_chat_input_html
    rendered = append_once_chat_input_html('test', '</script><script>alert(1)</script>', 'paragraph')
    assert rendered.count('</script>') == 1
    assert '\\u003c/script>' in rendered
