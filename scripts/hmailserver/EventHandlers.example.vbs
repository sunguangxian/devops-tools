' hMailServer EventHandlers.vbs 示例
' 用途：在 SMTP DATA 接收完成后的 OnAcceptMessage 中，把用户实际提交的邮件
'       复制到 devops-tools 本地事件队列，再通知统一服务立即消费。
'
' 部署前请修改下面三个配置值。

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

Function BuildQueueBaseName(ByVal sourcePath, ByVal sessionId)
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

    BuildQueueBaseName = sourceBase & "_" & CStr(sessionId) & "_" & stamp
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

    ' 对周报“发送后归档”的场景，默认只捕获经过 SMTP AUTH 的用户提交邮件。
    ' 这样外部服务器投递到本机的普通来信不会进入归档队列。
    If DEVOPS_REQUIRE_AUTHENTICATED Then
        If Not oClient.Authenticated Then Exit Sub
    End If

    Dim fso, baseName, tempPath, finalPath
    EnsureFolder DEVOPS_QUEUE_DIR

    Set fso = CreateObject("Scripting.FileSystemObject")
    baseName = BuildQueueBaseName(oMessage.Filename, oClient.SessionID)
    tempPath = DEVOPS_QUEUE_DIR & "\" & baseName & ".tmp"
    finalPath = DEVOPS_QUEUE_DIR & "\" & baseName & ".eml"

    ' 先完整复制到 .tmp，再发布为 .eml。
    ' Python 只扫描 *.eml，不会读取正在复制的半成品。
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
            ' HTTP 通知失败也不会丢邮件：.eml 已留在队列中，Python 会定期重试。
            NotifyDevOpsService
        End If
    End If

    Set fso = Nothing
    On Error GoTo 0
End Sub
