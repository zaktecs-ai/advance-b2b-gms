from pathlib import Path


def test_final_pass_marker_file_content():
    p = Path('docs/final-pass.txt')
    assert p.exists()
    assert p.read_text(encoding='utf-8').strip() == 'FINAL_PASS_BANK'
