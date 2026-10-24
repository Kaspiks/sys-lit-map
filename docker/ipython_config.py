c = get_config()  # noqa: F821

# Edits to src/litmap on the Mac are picked up without restarting IPython
c.InteractiveShellApp.extensions = ["autoreload"]
c.InteractiveShellApp.exec_lines = ["%autoreload 2"]

c.TerminalInteractiveShell.confirm_exit = False
c.HistoryManager.enabled = True
