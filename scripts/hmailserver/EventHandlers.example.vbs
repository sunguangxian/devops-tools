' hMailServer EventHandlers.vbs 示例
' 用途：在 OnDeliverMessage 中复制当前邮件 .eml 到 devops-tools 本地队列，
'       然后通知统一服务立即消费队列。
'
' 部署前请修改下面三个常量：
'   DEVOPS_QUEUE_DIR  - 必须与 weekly_report_sync.yaml 的 hmail_event.queue_dir 指向同一目录
'   DEVOPS_EVENT_URL  - devops-tools 本机 HTTP 地址
'   DEVOPS_EVENT_KEY  - 必须与 weekly_report_sync.yaml 的 hmail_event.api_key 一致

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

Function BuildQueueFileName(ByVal messageId)
    Dim nowValue, stamp
    nowValue = Now
    stamp = Year(nowValue) _
        & Right("0" & Month(nowValue), 2) _
        & Right("0" & Day(nowValue), 2) _
        & "_" _
        & Right("0" & Hour(nowValue), 2) _
        & Right("0" & Minute(nowValue), 2) _
        & Right("0" & Second(nowValue), 2)

    BuildQueueFileName = CStr(messageId) & "_" & stamp & ".eml"
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

    Dim fso, destinationPath
    EnsureFolder DEVOPS_QUEUE_DIR

    Set fso = CreateObject("Scripting.FileSystemObject")
    destinationPath = DEVOPS_QUEUE_DIR & "\" & BuildQueueFileName(oMessage.ID)

    ' 先复制快照。即使 Python 服务暂时不可用，.eml 仍留在队列中，服务恢复后可继续处理。
    fso.CopyFile oMessage.Filename, destinationPath, True

    If Err.Number <> 0 Then
        EventLog.Write "DevOps mail event copy failed: " & Err.Description _
            & "; source=" & oMessage.Filename _
            & "; target=" & destinationPath
        Err.Clear
    Else
        NotifyDevOpsService
    End If

    Set fso = Nothing
    On Error GoTo 0
End Sub
