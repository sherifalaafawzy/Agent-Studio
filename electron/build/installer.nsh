; Uninstall keeps your data (chats, agents, settings, keys) unless you say otherwise. Silent uninstalls (/S) always keep it.
!macro customUnInstall
  IfSilent keep_data
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "Also delete your Agent Studio data (chats, agents, settings, saved keys and installed tools) in $APPDATA\Agent Studio?$\r$\n$\r$\nChoose No to keep it for a later install." IDYES delete_data IDNO keep_data
  delete_data:
    RMDir /r "$APPDATA\Agent Studio"
  keep_data:
!macroend
