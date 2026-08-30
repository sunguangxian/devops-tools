' hMailServer EventHandlers.vbs example.
' Copies authenticated SMTP submissions to the devops-tools event queue from
' OnAcceptMessage, then sends a lightweight notification to the local service.
'
' Update these settings before deployment.

Const DEVOPS_QUEUE_DIR = "C:\server-service\devops-tools\data\mail_event_queue"
Const DEVOPS_EVENT_URL = "http://127.0.0.1:5000/event/hmailserver"
Const DEVOPS_EVENT_KEY = "replace-with-a-long-random-event-api-key"
Const DEVOPS_REQUIRE_AUTHENTICATED = True

Sub EnsureFolder(ByVal folderPath)
    Dim fso, parentPath
    Set fso = CreateObject("Scripting.FileSystemObject")

    If fso.FolderExists(folderPath) Then Exit Sub

    parentPath = fso.GetParentFolderName(folderPath)
    If Len(parentPath) > 0 And Not fso.FolderExists(parentPath) Then
        EnsureFolder parentPath
    End If

    If Not fso.FolderExists(folderPath) Then
        fso.CreateFolder folderPath
    End If
End Sub

Function BuildQueueBaseName(ByVal sourcePath)
    Dim fso, sourceBase, nowValue, stamp
    Set fso = CreateObject("Scripting.FileSystemObject")

    sourceBase = fso.GetBaseName(sourcePath)
    sourceBase = Replace(sourceBase, "{", "")
    sourceBase = Replace(sourceBase, "}", "")

    nowValue = Now
    stamp = Year(nowValue) _
        & Right("0" & Month(nowValue), 2) _
        & Right("0" & Day(nowValue), 2) _
        & "_" _
        & Right("0" & Hour(nowValue), 2) _
        & Right("0" & Minute(nowValue), 2) _
        & Right("0" & Second(nowValue), 2)

    BuildQueueBaseName = sourceBase & "_" & stamp
    Set fso = Nothing
End Function

Sub NotifyDevOpsService()
    On Error Resume Next

    Dim http
    Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    http.setTimeouts 1000, 1000, 1000, 2000
    http.open "POST", DEVOPS_EVENT_URL, False
    http.setRequestHeader "X-API-Key", DEVOPS_EVENT_KEY
    http.setRequestHeader "Content-Type", "text/plain"
    http.send "hmailserver-event"

    If Err.Number <> 0 Then
        EventLog.Write "DevOps mail event notify failed: " & Err.Description
        Err.Clear
    ElseIf http.status < 200 Or http.status >= 300 Then
        EventLog.Write "DevOps mail event notify returned HTTP " & CStr(http.status)
    End If

    Set http = Nothing
    On Error GoTo 0
End Sub

Sub OnAcceptMessage(oClient, oMessage)
    On Error Resume Next

    ' Username is available on classic hMailServer releases after SMTP AUTH.
    ' An empty username means that the SMTP session was not authenticated.
    If DEVOPS_REQUIRE_AUTHENTICATED Then
        If Len(Trim(CStr(oClient.Username))) = 0 Then Exit Sub
    End If

    Dim fso, baseName, tempPath, finalPath
    EnsureFolder DEVOPS_QUEUE_DIR

    Set fso = CreateObject("Scripting.FileSystemObject")
    baseName = BuildQueueBaseName(oMessage.Filename)
    tempPath = DEVOPS_QUEUE_DIR & "\" & baseName & ".tmp"
    finalPath = DEVOPS_QUEUE_DIR & "\" & baseName & ".eml"

    ' Publish only complete files. The Python worker scans *.eml, not *.tmp.
    If fso.FileExists(tempPath) Then fso.DeleteFile tempPath, True
    fso.CopyFile oMessage.Filename, tempPath, True

    If Err.Number <> 0 Then
        EventLog.Write "DevOps mail event copy failed: " & Err.Description _
            & "; source=" & oMessage.Filename _
            & "; target=" & tempPath
        Err.Clear
    Else
        If fso.FileExists(finalPath) Then fso.DeleteFile finalPath, True
        fso.MoveFile tempPath, finalPath

        If Err.Number <> 0 Then
            EventLog.Write "DevOps mail event publish failed: " & Err.Description _
                & "; temp=" & tempPath _
                & "; target=" & finalPath
            Err.Clear
        Else
            ' The queued .eml remains available for retry if notification fails.
            NotifyDevOpsService
        End If
    End If

    Set fso = Nothing
    On Error GoTo 0
End Sub
