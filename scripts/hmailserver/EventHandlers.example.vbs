' hMailServer EventHandlers.vbs 示例
' 用途：在 OnDeliverMessage 中复制当前邮件快照到 devops-tools 本地队列，
'       然后通知统一服务立即消费队列。
'
' 部署前请修改下面三个常量。

Const DEVOPS_QUEUE_DIR = "C:\server-service\devops-tools\data\mail_event_queue"
Const DEVOPS_EVENT_URL = "http://127.0.0.1:5000/event/hmailserver"
Const DEVOPS_EVENT_KEY = "replace-with-a-long-random-event-api-key"

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

Function BuildQueueBaseName(ByVal messageId)
    Dim nowValue, stamp
    nowValue = Now
    stamp = Year(nowValue) _
        & Right("0" & Month(nowValue), 2) _
        & Right("0" & Day(nowValue), 2) _
        & "_" _
        & Right("0" & Hour(nowValue), 2) _
        & Right("0" & Minute(nowValue), 2) _
        & Right("0" & Second(nowValue), 2)

    BuildQueueBaseName = CStr(messageId) & "_" & stamp
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

Sub OnDeliverMessage(oMessage)
    On Error Resume Next

    Dim fso, baseName, tempPath, finalPath
    EnsureFolder DEVOPS_QUEUE_DIR

    Set fso = CreateObject("Scripting.FileSystemObject")
    baseName = BuildQueueBaseName(oMessage.ID)
    tempPath = DEVOPS_QUEUE_DIR & "\" & baseName & ".tmp"
    finalPath = DEVOPS_QUEUE_DIR & "\" & baseName & ".eml"

    ' 先完整复制到 .tmp，再原子式发布为 .eml。
    ' Python 只扫描 *.eml，因此不会读到复制到一半的邮件。
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
            ' 即使 HTTP 通知失败，.eml 仍留在队列中，消费者会按 retry_interval_seconds 重试。
            NotifyDevOpsService
        End If
    End If

    Set fso = Nothing
    On Error GoTo 0
End Sub
