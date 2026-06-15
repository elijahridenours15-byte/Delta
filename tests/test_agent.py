import os
from agent.agent import generate_project, run_instruction


def test_generate_basic_script():
    name, files = generate_project('simple script', 'test_project')
    assert isinstance(name, str)
    assert isinstance(files, dict) or isinstance(files, dict)


def test_run_instruction_dry():
    res = run_instruction('create a script', project_name='test_project2', execute=False, base_dir=os.path.join(os.getcwd(), 'generated_test'))
    assert 'project_name' in res
    assert 'files' in res
