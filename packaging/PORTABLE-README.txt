Research Assistant 0.7.2 - Windows x64 portable release
========================================================

Start ResearchAssistant.exe to open the desktop application. It starts the
persistent Core automatically. The Core remains in the Windows notification
area after the GUI closes. Double-click its tray icon, or right-click and choose
"打开 Research Assistant", to reopen the GUI. Choose "退出 Core" to stop it.

Application data is stored at:
  %LOCALAPPDATA%\ResearchAssistant\data

To reuse an existing source-tree index, close the Core and copy the contents of
the repository's data directory into that directory. PDF paths stored in the
index must still be valid. Alternatively, start Zotero and build a new index in
the GUI. Research Assistant never reads or writes zotero.sqlite.

Codex MCP registration:
  codex mcp add research-assistant-evidence -- "FULL\PATH\ResearchAssistantMCP.exe"

The MCP executable is a lightweight stdio proxy. ResearchAssistantCore.exe must
be running; it can be started directly and managed through its tray icon.

This is a portable folder, not an installer. Keep the complete folder together;
the EXEs require the adjacent _internal directory.
