Attribute VB_Name = "ExportImportVBProject"
' Adapted from: https://github.com/sandraros/VBA-VBProject-various-code
' For use in CATIA V5 VBA IDE
'
' Requirements (Tools > References in VBA IDE):
'   - Microsoft Visual Basic for Applications Extensibility  (VBE6EXT.OLB)
'   - Microsoft Scripting Runtime  (scrrun.dll)
'
' Usage:
'   1. Import this file into your .catvba project once.
'   2. Run ExportProject  ->  all modules are saved as .bas / .frm / .cls
'      next to the .catvba file.
'   3. Edit those files in VS Code (encoding: GBK / ANSI).
'   4. Run ImportProject  ->  edited files are loaded back into the project.
'   5. Save the project.
'
Option Explicit

Const cCurrentModuleName As String = "ExportImportVBProject"

' ©¤©¤©¤ Main entry points (run these from the VBA IDE) ©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤

Public Sub ExportProject()
    ' Export all modules of the active project to .bas / .frm / .cls files.
    Dim sFolder As String
    sFolder = GetFolderPath(VBE.ActiveVBProject.FileName)
    Call ExportVBProjectDialog(sFolder, VBE.ActiveVBProject.Name, VBE.ActiveVBProject)
End Sub

Public Sub ImportProject()
    ' Import .bas / .frm / .cls files from the project folder back into the project.
    Dim sFolder As String
    sFolder = GetFolderPath(VBE.ActiveVBProject.FileName)
    Call ImportVBProjectDialog(sFolder, VBE.ActiveVBProject.Name, VBE.ActiveVBProject)
End Sub

' ©¤©¤©¤ Core export / import (unchanged from original) ©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤

Public Sub ExportVBProjectDialog(isFolderPath As String, isFileName As String, ioVBProject As Object)

    Dim iErrNum As Long
    Dim oFiles As Collection
    Dim sAnswer As String

    Call CheckFolderFreeOfUnrelatedVBComponentFiles(isFolderPath, isFileName, ioVBProject)

    On Error Resume Next
    Call ExportVBProject(isFolderPath, isFileName, ioVBProject, False)
    iErrNum = Err.Number
    On Error GoTo 0
    If iErrNum <> 0 Then
        Set oFiles = GetVBFiles(AdjustFilePath(isFolderPath), ioVBProject.VBComponents, False)
        sAnswer = InputBox(oFiles.Count & " files already exist (out of " & ioVBProject.VBComponents.Count & "), replace them? (type ""YES"" to confirm)", , "YES")
        If sAnswer <> "YES" Then
            Err.Raise 64235, , "Aborted by user"
        Else
            Call ExportVBProject(isFolderPath, isFileName, ioVBProject, True)
        End If
    End If

End Sub

Public Sub ExportVBProject(isFolderPath As String, isFileName As String, ioVBProject As Object, ibReplaceAllVBComponents As Boolean)

    Dim sFolderPath As String
    Dim oFiles As Collection
    Dim oVBComponent As Object
    Dim sExt As String

    If isFolderPath = "" Then
        Err.Raise 64231, , "Document '" & isFileName & "' must be saved before export"
    End If

    sFolderPath = AdjustFilePath(isFolderPath)

    Set oFiles = GetVBFiles(sFolderPath, ioVBProject.VBComponents, False)
    If oFiles.Count > 0 And Not ibReplaceAllVBComponents Then
        Err.Raise 64233, , "Files already exist, export stopped"
    End If

    For Each oVBComponent In ioVBProject.VBComponents
        sExt = GetFileExtension(oVBComponent.Type)
        If sExt <> "" Then
            oVBComponent.Export sFolderPath & oVBComponent.Name & "." & sExt
        End If
    Next

End Sub

Public Sub ImportVBProjectDialog(isFolderPath As String, isFileName As String, ioVBProject As Object)

    Dim iTotalExisting As Integer
    Dim iTotalNew As Integer
    Dim oFiles As Collection
    Dim oFile As Collection
    Dim sAnswer As String
    Dim sName As String

    Set oFiles = GetVBFiles(AdjustFilePath(isFolderPath), ioVBProject.VBComponents, True)
    For Each oFile In oFiles
        Call GetVBFile(oFile, sName)
        If sName <> cCurrentModuleName Then
            If VBComponentExists(ioVBProject.VBComponents, sName) Then
                iTotalExisting = iTotalExisting + 1
            Else
                iTotalNew = iTotalNew + 1
            End If
        End If
    Next

    sAnswer = InputBox("IMPORT: add " & iTotalNew & " new + replace " & iTotalExisting & " existing modules. Continue? (type ""YES"")", , "NO")
    If sAnswer <> "YES" Then
        Err.Raise 64235, , "Aborted by user"
    End If
    Call ImportVBProject(isFolderPath, isFileName, ioVBProject, True)

End Sub

Public Sub ImportVBProject(isFolderPath As String, isFileName As String, ioVBProject As Object, Optional ibReplaceAll As Boolean)

    Dim oVBProject As Object
    Dim sFolderPath As String
    Dim sExt As String
    Dim oFiles As Collection
    Dim oFile As Collection
    Dim sName As String

    If isFolderPath = "" Then
        Err.Raise 64231, , "Document must be saved before import"
        Exit Sub
    End If

    Set oVBProject = ioVBProject
    sFolderPath = AdjustFilePath(isFolderPath)
    Set oFiles = GetVBFiles(sFolderPath, ioVBProject.VBComponents, True)

    For Each oFile In oFiles
        Call GetVBFile(oFile, sName, sExt)
        If sName <> cCurrentModuleName Then
            If VBComponentExists(ioVBProject.VBComponents, sName) Then
                If Not ibReplaceAll Then
                    Err.Raise 64234, , "Module '" & sName & "' already exists"
                End If
                Call RemoveVBComponent(oVBProject.VBComponents, ioVBProject.VBComponents(sName))
            End If
            oVBProject.VBComponents.Import sFolderPath & sName & "." & sExt
        End If
    Next

End Sub

' ©¤©¤©¤ Helpers (unchanged from original) ©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤©¤

Public Sub CheckFolderFreeOfUnrelatedVBComponentFiles(isFolderPath As String, isFileName As String, ioVBProject As Object)

    Dim oFiles As Collection
    Dim oFile As Collection
    Dim sName As String
    Dim oFSFile As Object

    If isFolderPath = "" Then
        Err.Raise 64231, , "Document '" & isFileName & "' must be saved"
    End If

    Set oFiles = GetVBFiles(AdjustFilePath(isFolderPath), ioVBProject.VBComponents, True)
    For Each oFile In oFiles
        Call GetVBFile(oFile, sName, , , , oFSFile)
        If Not VBComponentExists(ioVBProject.VBComponents, sName) Then
            Err.Raise 64232, , "Folder contains unrelated VB files (e.g. '" & oFSFile.Name & "') ¡ª remove them first"
        End If
    Next

End Sub

Public Sub RemoveVBComponent(ioVBComponents As Object, ioVBComponent As Object)
    ioVBComponents.Remove ioVBComponent
End Sub

Function GetFileExtension(iiType As Integer) As String
    Select Case iiType
        Case 2, 100:  GetFileExtension = "cls"
        Case 3:       GetFileExtension = "frm"
        Case 1:       GetFileExtension = "bas"
        Case Else:    GetFileExtension = ""
    End Select
End Function

Public Function VBComponentExists(ioVBComponents As Object, isName As String) As Boolean
    On Error GoTo notFound
    VBComponentExists = True
    IsObject ioVBComponents(isName)
    Exit Function
notFound:
    VBComponentExists = False
End Function

Public Sub GetVBFile( _
    ioCollFile As Collection, _
    Optional esName As String, _
    Optional esExt As String, _
    Optional eiType As Integer, _
    Optional ebExists As String, _
    Optional eoFSFile As Object)
    esName   = ioCollFile(1)
    esExt    = ioCollFile(2)
    eiType   = ioCollFile(3)
    ebExists = ioCollFile(4)
    Set eoFSFile = ioCollFile(5)
End Sub

Function GetVBFiles(isFolder As String, ioVBComponents As Object, Optional ibIncludeUnrelated As Boolean = False) As Collection

    Dim oAll As Collection
    Dim oOne As Collection
    Dim oFS As Object
    Dim oFSFolder As Object
    Dim oFSFile As Object
    Dim iType As Integer
    Dim sName As String
    Dim bExists As Boolean

    Set oAll = New Collection
    Set oFS = CreateObject("Scripting.FileSystemObject")
    Set oFSFolder = oFS.GetFolder(isFolder)

    For Each oFSFile In oFSFolder.Files
        iType = GetVBComponentType(oFSFile.Name)
        If iType <> -1 Then
            sName = Left(oFSFile.Name, InStrRev(oFSFile.Name, ".") - 1)
            bExists = VBComponentExists(ioVBComponents, sName)
            If ibIncludeUnrelated Or bExists Then
                Set oOne = New Collection
                oOne.Add sName
                oOne.Add Mid(oFSFile.Name, InStrRev(oFSFile.Name, ".") + 1)
                oOne.Add iType
                oOne.Add bExists
                oOne.Add oFSFile
                oAll.Add oOne
            End If
        End If
    Next

    Set GetVBFiles = oAll

End Function

Function GetVBComponentType(isFileName As String) As Integer
    Select Case Right(isFileName, 4)
        Case ".bas": GetVBComponentType = 1
        Case ".cls": GetVBComponentType = 2
        Case ".frm": GetVBComponentType = 3
        Case Else:   GetVBComponentType = -1
    End Select
End Function

Function GetFolderPath(isFilePath As String) As String
    Dim pos As Integer
    pos = InStrRev(isFilePath, "\")
    If pos = 0 Then pos = InStrRev(isFilePath, "/")
    GetFolderPath = Left(isFilePath, pos)
End Function

Function AdjustFilePath(isFilePath As String) As String
    ' No OneDrive handling needed for CATIA ¡ª return as-is, ensure trailing slash
    Dim s As String
    s = isFilePath
    If Len(s) > 0 Then
        If Right(s, 1) <> "\" And Right(s, 1) <> "/" Then
            s = s & "\"
        End If
    End If
    AdjustFilePath = s
End Function
