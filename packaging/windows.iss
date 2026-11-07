; Per-user installation. Personal data lives outside {app} and is never removed.
[Setup]
AppId={{B781B529-36A4-49E9-A696-CF5C1038D7D9}
AppName=招聘雷达
AppVersion={#AppVersion}
AppPublisher=HireWatch
AppPublisherURL=https://github.com/SongLin99/HireWatch
DefaultDirName={localappdata}\Programs\HireWatch
DefaultGroupName=招聘雷达
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ReleaseDir}
OutputBaseFilename=HireWatch-{#AppVersion}-Windows-x64-Setup
SetupIconFile={#ProjectDir}\assets\hirewatch.ico
UninstallDisplayIcon={app}\招聘雷达.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
CloseApplications=yes
CloseApplicationsFilter=招聘雷达.exe
RestartApplications=no
UninstallDisplayName=招聘雷达 HireWatch

[Languages]
Name: "zh"; MessagesFile: "compiler:Default.isl"

[LangOptions]
LanguageName=简体中文
LanguageID=$0804
DialogFontName=Microsoft YaHei UI
TitleFontName=Microsoft YaHei UI
WelcomeFontName=Microsoft YaHei UI

[Messages]
SetupWindowTitle=招聘雷达安装程序
WelcomeLabel1=欢迎安装招聘雷达
WelcomeLabel2=安装包已包含运行环境和采集浏览器，无需配置 Python、Node.js 或 API Key。%n%n点击“下一步”开始安装。安装后可立即使用采集、搜索和收藏。
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonInstall=安装(&I)
ButtonCancel=取消(&C)
ButtonFinish=完成(&F)
WizardInstalling=正在安装招聘雷达
InstallingLabel=正在安装程序及内置运行环境，请稍候。
FinishedHeadingLabel=招聘雷达已安装完成
FinishedLabelNoIcons=现在可以打开招聘雷达。首次使用自带教程，AI 和微信提醒均可跳过。
StatusExtractFiles=正在安装文件……

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userdesktop}\招聘雷达"; Filename: "{app}\招聘雷达.exe"; WorkingDir: "{app}"
Name: "{userprograms}\招聘雷达"; Filename: "{app}\招聘雷达.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\招聘雷达.exe"; Description: "立即打开招聘雷达"; Flags: postinstall nowait skipifsilent
