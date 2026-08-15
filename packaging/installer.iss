#define MyAppName "PDH-PKG"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "PDH"
#define MyAppExeName "PDH-PKG.exe"

[Setup]
AppId={{8F1B4F3D-4A32-4C8A-9E1C-0D0E0F000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
SetupIconFile=resources\icon.ico
DefaultDirName={autopf}\PDH-PKG
DisableProgramGroupPage=yes
OutputDir=..\output
OutputBaseFilename=PDH-PKG-Setup-{#MyAppVersion}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\app\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Types]
Name: "full"; Description: "完整安装（推荐）：程序核心 + 内置向量模型，离线本地向量化开箱即用"
Name: "compact"; Description: "简洁安装：仅程序核心，不安装内置模型，首次使用需联网下载或改用 Ollama/API"
Name: "custom"; Description: "自定义安装：程序核心固定，可手动勾选是否安装内置模型"

[Components]
Name: "core"; Description: "程序核心（应用、内置 Qdrant 与运行环境）"; Types: full compact custom; Flags: fixed
#ifexist "..\packaging\resources\models"
Name: "model"; Description: "内置向量模型 bge-small-zh-v1.5（约91MB，离线可用）"; Types: full custom
#endif
#ifexist "..\packaging\resources\neo4j"
Name: "neo4j"; Description: "Neo4j 知识图谱组件（约500MB，含 Java 运行时）"; Types: full custom; ExtraDiskSpaceRequired: 524288000
#endif

[Files]
Source: "..\dist2\PDH-PKG\*"; DestDir: "{app}\app"; Flags: recursesubdirs ignoreversion; Components: core
#ifexist "..\packaging\resources\models"
Source: "..\packaging\resources\models\*"; DestDir: "{app}\models"; Flags: recursesubdirs ignoreversion; Components: model
#endif
#ifexist "..\packaging\resources\neo4j"
Source: "..\packaging\resources\neo4j\*"; DestDir: "{app}\neo4j"; Flags: recursesubdirs ignoreversion; Components: neo4j
#endif

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\app\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\app\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\app\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if MsgBox(
      '是否同时删除个人数据？' + #13#10 + #13#10 +
      '删除后将无法恢复：对话记录、文档、知识库、Qdrant 数据。',
      mbConfirmation,
      MB_YESNO
    ) = IDYES then
      DelTree(ExpandConstant('{localappdata}\PDH-PKG'), True, True, True);
  end;
end;
