# 串行控制台教程

串行控制台是 SSH/RDP 都连不上时的救命通道：它由 OCI 控制台服务维持一条
持久的字符流，和实例的网络、系统状态无关，机器卡死、改错防火墙、忘密码，
都能从这里进去。面板的「串行控制台」页提供两个入口：

- **串行连接 (SSH)**：生成一条 SSH 命令，在你本机终端执行后进入串口；
- **图形控制台 (VNC)**：新窗口直接打开实例画面，适合看图形界面。

> ⚠️ 一台实例同时只能挂 **一个** 控制台连接：串行和 VNC 互顶——新建一个
> 会自动清理另一个。用完记得在面板点「删除连接」。

## 1. 建立串行连接

1. 面板「串行控制台」页选实例 → 点「创建串行连接 (SSH)」；
2. 点「⬇ 下载私钥」把私钥保存到本机；
3. 在放私钥的目录里打开终端，粘贴面板给出的 SSH 命令执行。

### Windows 注意（PowerShell）

- 用 **PowerShell**，不要用 CMD：CMD 不认命令里的单引号；
- Win10/11 自带 OpenSSH 客户端，先输 `ssh -V` 确认有版本号；
- **每次新下载的私钥都要先收紧权限**，否则 ssh 会报
  `UNPROTECTED PRIVATE KEY FILE` 拒绝加载（下载目录的继承权限太松）：

  ```powershell
  icacls .\console_*.pem /inheritance:r
  icacls .\console_*.pem /remove *S-1-5-11
  icacls .\console_*.pem /grant:r "$($env:USERNAME):F"
  ```

### macOS / Linux 注意

```bash
chmod 600 console_*.pem
```

连上后屏幕没输出是正常的：串口只在系统有输出时才滚动，敲一下回车试试。

## 2. 看重启和引导过程

串行流在**软重启时不断开**（VNC 转发通道则在断电瞬间必断、重连又慢，
抓不住引导画面），所以看重启、进固件、进 GRUB 都用串行：

1. 先按第 1 节连上串行并**保持窗口开着**；
2. 再去「实例管理」点**重启**（软重启；停止+启动是硬断电，串行也会断），
   或在另一个 SSH 会话里执行 `sudo reboot`；
3. 固件阶段连按 **Esc** 可进 UEFI 菜单（Boot Manager 选启动盘）。

事后只想看引导日志的话，用面板的「查看控制台历史」，不用保持连接。

## 3. 方法一：抓 GRUB 菜单编辑启动项

1. 重启后紧盯串口，在固件画面过后**连按 Esc**（或按住 Shift）调出
   GRUB 菜单；
2. 菜单出现后按 **e** 编辑默认启动项；
3. 找到以 `linux` 开头的那行，到**行尾**追加需要的内核参数
   （改密码的例子：` rw init=/bin/bash`，注意前面的空格）；
4. 按 **Ctrl+X** 或 **F10** 以改后的参数启动（只对这一次生效，不落盘）。

> 抓不住是常态：不少云镜像 `GRUB_TIMEOUT=0`、菜单全隐藏，从开机到进系统
> 只有一瞬间。抓不到就走方法二，在 GRUB 命令行手动启动，结果一样。

## 4. 方法二：在 grub> 命令行手动设置启动项

按 Esc 抓菜单失败时，往往会掉进 `grub>` 命令行（或在菜单界面按 **c**
进入）。在这里可以直接指定内核手动启动：

### 4.1 先探明 /boot 在哪个分区

```
ls
ls (hd0,gpt1)/boot/
ls (hd0,gpt14)/boot/
ls (hd0,gpt15)/boot/
```

哪个分区能列出 `vmlinuz-...` 就用哪个。常见布局（Ubuntu 云镜像）：
`gpt1` 是根分区（`/boot` 是它的子目录），`gpt14` 是 BIOS 引导分区
（报 unknown filesystem 正常），`gpt15` 是 EFI 分区（只有 `EFI/` 目录）。

输命令时多用 **TAB 补全**：`linux /boot/vm` + TAB 能补全文件名，
补得出来就说明分区找对了。

### 4.2 可选：用 configfile 把菜单调回来

```
set root=(hd0,gpt1)
configfile /boot/grub/grub.cfg
```

> 坑：镜像的 grub.cfg 若是 `timeout=0`，configfile 一加载就直接按默认项
> 启动、菜单不显示——遇到这种情况不要纠结，直接用 4.3 手动启动。

### 4.3 手动启动三行

```
set root=(hd0,gpt1)
linux /boot/vmlinuz-<版本> root=/dev/sda1 rw console=tty1 console=ttyS0 rootdelay=30 init=/bin/bash
initrd /boot/initrd.img-<版本>
boot
```

说明：

- `<版本>` 用 TAB 补全或照启动日志里的 `BOOT_IMAGE` 抄；
- `root=` 用设备名 `/dev/sda1` 可避免手敲一长串 UUID 出错；想用 UUID 也
  行，照日志里 `root=UUID=...` 逐字抄；
- `rootdelay=30` 给 iSCSI 引导盘留足出现时间（OCI 的系统盘多是 iSCSI
  网络盘，引导时要先联网登录，慢了就会报找不到根分区）；
- `init=/bin/bash` 换成你需要的参数组合（见第 6 节改密码实战）；
- 输错了回车只会报错，回到 `grub>` 重输即可，不会把系统改坏。

## 5. 掉进 initramfs (BusyBox) 怎么办

如果屏幕停在 `(initramfs)` 提示符，并伴随
`Gave up waiting for root file system device` / `UUID=... does not exist`，
说明引导阶段没挂上根分区，掉进了内存里的紧急小系统——**这里没有
`passwd` 命令，改不了密码**，先想办法出去：

1. 输 `exit` 回车：它会继续尝试挂载根分区，这会儿 iSCSI 盘多半已经
   连上，能直接续上启动；又掉回来就再 `exit` 一次；
2. 两次还不行，在里面查清设备再重启：`blkid`、`ls /dev/sd*` 看根分区
   到底叫什么，然后按 4.3 用确切参数重来；
3. 想直接重启：`reboot -f`；没反应就
   `echo s > /proc/sysrq-trigger` 后再 `echo b > /proc/sysrq-trigger`；
   兜底是去面板「实例管理」对这台实例**强制重启**。

## 6. 实战：忘记密码时重置密码

1. 按第 2 节连上串行并保持，重启实例；
2. 按第 3 节抓菜单按 `e` 编辑，或按第 4 节在 `grub>` 手动启动——
   共同点都是在内核参数里带上 `rw init=/bin/bash`；
3. 启动后会停在一个**不需要密码的 root bash** 提示符，改密码：

   ```bash
   mount -o remount,rw /
   ls /home          # 先确认要改的用户名（Ubuntu 云镜像默认是 ubuntu）
   passwd ubuntu     # 改 root 就写 passwd root
   sync
   reboot -f         # 注意：此时 PID 1 是 bash，普通 reboot 不管用
   ```

4. 重启后是正常启动（GRUB 改动只对那一次生效），用新密码登录。

> 若 `passwd` 报 `Authentication token manipulation error`，
> 是根分区还是只读——先执行上面的 `mount -o remount,rw /` 再改。

## 7. 其他注意事项

- 控制台横幅（`IMPORTANT: Use a console connection to troubleshoot...`）
  出现说明两跳 SSH 已经通了，后面的报错再按具体信息定位；
- 串行连接对实例性能无影响，但闲置连接会占着第 1 节说的唯一名额，
  救完机器请回面板删除连接；
- 图形控制台 (VNC) 的适用场景是系统起来后的图形界面（如 Windows 登录）
  以及看 OS 启动后半程，不适合抓固件/GRUB。
