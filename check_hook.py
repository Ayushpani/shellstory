"""
Test spawning a subshell in Windows to see if the hook works.
"""
import subprocess
from pathlib import Path

hook = Path(r"C:\Users\Ayush\.shellstory\sessions\ce76b46f-031a-421b-ac53-8dd2cc3b11fa_hook.ps1")
if hook.exists():
    print("Hook exists. Try running it via powershell -NoExit -Command \". 'path'\"")
