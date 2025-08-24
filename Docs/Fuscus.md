Main-
https://ntpsec.org/    (New)
https://docs.ntpsec.org/latest/quick.html
    https://downloads.nwtime.org/ntp/    (OG)

Guis-
http://timesynctool.com/    (Grosse time)
https://www.meinbergglobal.com/english/sw/ntp.htm#ntp_stable    (Fein Time)
  https://www.meinbergglobal.com/download/ntp/docs/ntp_cheat_sheet.pdf   
 
    Configuring NTP client on Windows.    
        https://www.chrony.eu/setup/windows#package-windows
    0. Before start.    
        
    To configure NTP client you need Administrator privileges or Administartor's password. It is not possible to change configuration without sufficient privileges.    
        
    Below are several times four very similar commands with following meaning:    
        
       - 1st line: define variable myKey with working key.    
       - 2st line: show current value of parameter ( QUERY ),    
       - 3nd line: add (if not defined) or change parameter to new value ( ADD ),    
       - 4rd line: show new parameter value ( QUERY ).    
        
    In situation when command fails or existing value is alredy set to desired value, result of commands in second and fourth line will be the same.    
        
    Please note, that commands may be splitted to several lines depending of device browser width.    
    1.) Open elevated Administrator command prompt.    
        
       - on keyboard press windows key to open start menu,    
       - after start menu opens type "cmd",    
       - choose option "Run As Administrator",    
       - if current user has not enough privileges, password popup will open.    
        
    Windows elevated CMD prompt    
        
    Command "cmd" will start in "Elevated mode". This mode is recognized with user name "Administrator" in title of cmd window. Carefully enter (copy/paste) commands below from steps 2 to 8.    
    2.) Change MinPollInterval from default 0xa (2^10 = 1024sec) to 0x6 (2^6 = 64sec).    
        
       set myKey=HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config    
       REG QUERY %myKey% /v MinPollInterval    
       REG ADD   %myKey% /v MinPollInterval /t REG_DWORD /d 0x6 /f    
       REG QUERY %myKey% /v MinPollInterval    
        
    3.) Change MaxPollInterval from default 0xf (2^15 = 32768 sec) to 0xa (2^10 = 1024 sec).    
        
       set myKey=HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config    
       REG QUERY %myKey% /v MaxPollInterval    
       REG ADD   %myKey% /v MaxPollInterval /t REG_DWORD /d 0xa /f    
       REG QUERY %myKey% /v MaxPollInterval    
        
    4.) Change Client Type to NTP.    
        
       set myKey=HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Parameters    
       REG QUERY %myKey% /v Type    
       REG ADD   %myKey% /v Type /t REG_SZ /d NTP /f    
       REG QUERY %myKey% /v Type    
        
    5.) Change NtpClient state to enabled.    
        
       set myKey=HKLM\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpClient    
       REG QUERY %myKey% /v Enabled    
       REG ADD   %myKey% /v Enabled /t REG_DWORD /d 0x1 /f    
       REG QUERY %myKey% /v Enabled    
        
    6.) Register and restart NTP service.    
        
       w32tm /register    
       net stop  w32time    
       net start w32time    
        
    7.) Select IP protocol version.    
        
    In order to align server connection to your network configuration it is advisable to define IP protocol version for time synchronization. There are three possible variants - use one from list:    
        
        For dual stacked IPv4 and IPv6 connected device(s) use:    
        
            set myTld="pool.chrony.eu"    
        
        For IPv4 connected device(s) use:    
        
            set myTld="ipv4.pool.chrony.eu"    
        
        For and IPv6 connected device(s) use:    
        
            set myTld="ipv6.pool.chrony.eu"    
        
    8.) Configure NTP client.    
        
    Continue with this list of commands to configure Windoes NTP client:    
        
       set myServers="1.%myTld%,0x08 2.%myTld%,0x08 3.%myTld%,0x08 4.%myTld%,0x08"    
       set myKey=HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Parameters    
       REG QUERY %myKey%    
       w32tm /config /manualpeerlist:%myServers% /syncfromflags:manual /update    
       REG QUERY %myKey%    
        
    Description of operation    
        
    This procedure will enable NTP client, set minimum update interval to 64s (1min 4sec),     
    maximum update interval to 1024s (17min 3sec) and configure NTP client     
    depending on computer connectivity. Correction of MinPollInterval and MaxPollInterval     
    is necessary because default values are too coarse for built in CMOS clock, which may     
    have time drift of several seconds for default MaxPollInterval configured of 32768s     
    (9hours 6minutes 8seconds). NTP client will slowly drift time in computer to correct     
    NTP synchronized time. How long will computer run synchronization is dependent from current     
    skew from correct time.    
        
    Synchronization of computer time is maintained until computer is powered on.     
    Depending on duration of power off time, internal CMOS clock maintains coarse time.     
    At computer power on event NTP client reads configuration and starts with time synchronization.     
    If time difference between computer and reference source is reasonably small,     
    time is adjusted in several steps and time synchronization is achieved quickly.     
    For long power off periods, when there is sufficient time difference between CMOS battery time     
    and reference source synchronization time may be reasonably long.    
        
    chrony.eu logo    
    Official chrony.eu web site.(Op is not associated.)    
    All rights reserved.    
    Hosting by xservers.si    
    © 2020-2025 chrony.eu    
