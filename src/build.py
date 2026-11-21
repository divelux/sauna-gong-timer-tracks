#!/usr/bin/env python3
"""Builds the Sauna Gong Timer tracks: silence, then a soft gong that starts exactly at N:00.

1. The gong is synthesized here with numpy from a few decaying sine partials (the same recipe as the
   DIVELUX Sauna Timer Videos). No samples or recordings are used. Peak normalized to -3 dBFS, short fade at the end.
2. For each length, ONE ffmpeg process (nice -n 10) joins N minutes of digital silence and the gong and writes
   MP3 (128 kbps CBR, mono, 44.1 kHz) and Ogg Vorbis (q4).
3. Tags with mutagen (ID3v2.3 for MP3, Vorbis comments for Ogg).
4. check(): decodes every file back and verifies length, bitrate, silence before N:00, gong level, no clipping.
5. preview.png: waveform of the 5-minute track and a close-up of the gong.

Needs: ffmpeg with libmp3lame + libvorbis, pip install numpy mutagen matplotlib
License: audio CC BY 4.0, script MIT - DIVELUX, https://diveluxsaunahat.com
"""
import json
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SR = 44100
MINUTES = [5, 8, 10, 12, 15, 20]
GONG_S = 10.0                    # length of the tail after N:00
SECOND = 3.5                     # second, softer strike after the first
PEAK_DBFS = -3.0
SITE = 'https://diveluxsaunahat.com'
# (frequency Hz, amplitude, decay per second): a low, slightly beating fundamental and a few inharmonic partials
PARTIALS = [(174.0, 1.0, .55), (175.4, .6, .5), (262.3, .55, .8), (349.9, .45, 1.0), (468.0, .3, 1.5),
            (587.0, .22, 2.1), (741.0, .14, 3.0), (1012.0, .08, 5.0)]


def strike(n):
    t = np.arange(n) / SR
    x = sum(a * np.sin(2 * np.pi * fr * t) * np.exp(-d * t) for fr, a, d in PARTIALS)
    return x * (1 - np.exp(-45 * t))          # soft mallet: about 50 ms to full level, no click


def gong():
    n = int(GONG_S * SR)
    x = strike(n)
    k = int(SECOND * SR)
    x[k:] += .55 * strike(n - k)
    fade = int(2.0 * SR)
    x[-fade:] *= np.cos(np.linspace(0, np.pi / 2, fade)) ** 2
    x *= 10 ** (PEAK_DBFS / 20) / np.abs(x).max()
    return x


def write_wav(path, x):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.round(x * 32767).astype('<i2').tobytes())


def tag(path, minutes):
    title = f'Sauna Gong Timer - {minutes} Minutes'
    lic = 'CC BY 4.0 - https://creativecommons.org/licenses/by/4.0/'
    comment = f'Silence, then a soft gong at {minutes}:00. Made by DIVELUX, makers of wool sauna hats - {SITE}. {lic}'
    if path.endswith('.mp3'):
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, TRCK, TDRC, COMM, TCOP, WOAR, WXXX, TCON, ID3NoHeaderError
        try:
            t = ID3(path)
        except ID3NoHeaderError:
            t = ID3()
        t.delete(path)
        t = ID3()
        t.add(TIT2(encoding=3, text=title))
        t.add(TPE1(encoding=3, text='DIVELUX'))
        t.add(TALB(encoding=3, text='Sauna Gong Timers'))
        t.add(TRCK(encoding=3, text=f'{MINUTES.index(minutes) + 1}/{len(MINUTES)}'))
        t.add(TDRC(encoding=3, text='2026'))
        t.add(TCON(encoding=3, text='Ambient'))
        t.add(TCOP(encoding=3, text='2026 DIVELUX. ' + lic))
        t.add(COMM(encoding=3, lang='eng', desc='', text=comment))
        t.add(WOAR(url=SITE))
        t.add(WXXX(encoding=3, desc='License', url='https://creativecommons.org/licenses/by/4.0/'))
        t.save(path, v2_version=3)
    else:
        from mutagen.oggvorbis import OggVorbis
        a = OggVorbis(path)
        a.delete()
        a.update({'TITLE': title, 'ARTIST': 'DIVELUX', 'ALBUM': 'Sauna Gong Timers',
                  'TRACKNUMBER': str(MINUTES.index(minutes) + 1), 'DATE': '2026', 'GENRE': 'Ambient',
                  'LICENSE': lic, 'COPYRIGHT': '2026 DIVELUX', 'CONTACT': SITE, 'COMMENT': comment})
        a.save()


def encode(gong_wav, minutes):
    base = os.path.join(ROOT, f'sauna-gong-timer-{minutes:02d}-minutes')
    cmd = ['nice', '-n', '10', 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-threads', '1',
           '-f', 'lavfi', '-t', str(minutes * 60), '-i', f'anullsrc=r={SR}:cl=mono', '-i', gong_wav,
           '-filter_complex', '[0:a][1:a]concat=n=2:v=0:a=1,asplit=2[m][o]', '-map_metadata', '-1',
           '-map', '[m]', '-c:a', 'libmp3lame', '-b:a', '128k', '-ar', str(SR), '-ac', '1', base + '.mp3',
           '-map', '[o]', '-c:a', 'libvorbis', '-q:a', '4', '-ar', str(SR), '-ac', '1', base + '.ogg']
    subprocess.run(cmd, check=True)
    for ext in ('.mp3', '.ogg'):
        tag(base + ext, minutes)
    return base


def decode_tail(path, start, dur):
    """Mono float samples from `start` s, `dur` s long (one ffmpeg, nice)."""
    out = subprocess.run(['nice', '-n', '10', 'ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(start),
                          '-t', str(dur), '-i', path, '-f', 's16le', '-ac', '1', '-ar', str(SR), '-'],
                         check=True, capture_output=True).stdout
    return np.frombuffer(out, '<i2').astype(float) / 32768


def check(minutes):
    base = os.path.join(ROOT, f'sauna-gong-timer-{minutes:02d}-minutes')
    rep = {}
    for ext in ('.mp3', '.ogg'):
        p = base + ext
        pr = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', p],
                                       check=True, capture_output=True).stdout)
        fmt, st = pr['format'], pr['streams'][0]
        dur = float(fmt['duration'])
        x = decode_tail(p, minutes * 60 - 20, 30)              # 20 s before N:00 to the end
        pre, post = x[:int(19.5 * SR)], x[int(19.9 * SR):]
        onset = np.argmax(np.abs(x) > 10 ** (-40 / 20)) / SR - 20   # first sample above -40 dBFS, relative to N:00
        tags = fmt.get('tags', {}) or st.get('tags', {})
        r = dict(duration=round(dur, 2), bitrate=st.get('bit_rate') or fmt.get('bit_rate'), codec=st['codec_name'],
                 pre_max_dbfs=round(20 * np.log10(np.abs(pre).max() + 1e-12), 1),
                 gong_onset_s=round(onset, 3), gong_peak_dbfs=round(20 * np.log10(np.abs(post).max()), 2),
                 clipped=int((np.abs(x) >= .999).sum()), size_kb=os.path.getsize(p) // 1024,
                 title=tags.get('title') or tags.get('TITLE'), artist=tags.get('artist') or tags.get('ARTIST'))
        ok = (abs(dur - (minutes * 60 + GONG_S)) < .2 and r['pre_max_dbfs'] < -60 and abs(onset) < .06
              and r['gong_peak_dbfs'] < -.5 and not r['clipped'] and r['artist'] == 'DIVELUX')
        r['ok'] = bool(ok)
        rep[ext[1:]] = r
    return rep


def preview(gong_x):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    fd = os.path.join(ROOT, '..', '..', '_fonts')
    for fn in ('Inter-Regular.ttf', 'Inter-SemiBold.ttf', 'BricolageGrotesque-Bold.ttf'):
        fm.fontManager.addfont(os.path.join(fd, fn))
    paper, ink, muted, ember, line = '#f6f3ee', '#1b1512', '#6f655c', '#f0762e', '#e3ddd3'
    plt.rcParams.update({'font.family': 'Inter', 'text.color': ink, 'axes.edgecolor': line,
                         'xtick.color': muted, 'ytick.color': muted})
    fig = plt.figure(figsize=(16, 9), dpi=100, facecolor=paper)
    fig.text(.05, .9, 'Sauna Gong Timers', fontsize=40, fontfamily='Bricolage Grotesque 96pt ExtraBold', weight='bold')
    fig.text(.05, .845, 'Silence, then one soft gong at 5, 8, 10, 12, 15 or 20 minutes  ·  MP3 + OGG  ·  free under CC BY 4.0',
             fontsize=17, color=muted)
    # whole 5-minute track: envelope in 0.5 s blocks
    n = 5 * 60
    env = np.zeros(int((n + GONG_S) * 2))
    blk = SR // 2
    g = np.abs(gong_x)
    for i in range(len(g) // blk):
        env[n * 2 + i] = g[i * blk:(i + 1) * blk].max()
    ax = fig.add_axes([.05, .5, .9, .25], facecolor=paper)
    tt = np.arange(len(env)) / 2
    ax.fill_between(tt, -env, env, color=ink, step='mid', lw=0)
    ax.axhline(0, color=line, lw=1.5, zorder=0)
    ax.axvline(n, color=ember, lw=2)
    ax.text(n - 4, .62, 'gong at 5:00', color=ember, fontsize=15, ha='right', weight='semibold')
    ax.text(4, .62, 'silence', color=muted, fontsize=15)
    ax.set_xlim(0, n + GONG_S)
    ax.set_ylim(-.8, .8)
    ax.set_xticks(range(0, n + 1, 60))
    ax.set_xticklabels([f'{m}:00' for m in range(6)], fontsize=13)
    ax.set_yticks([])
    for s in ('top', 'right', 'left'):
        ax.spines[s].set_visible(False)
    ax.set_title('5-minute track', loc='left', fontsize=15, color=muted, pad=8)
    # the gong itself
    ax2 = fig.add_axes([.05, .12, .9, .25], facecolor=paper)
    t = np.arange(len(gong_x)) / SR
    step = 20
    ax2.plot(t[::step], gong_x[::step], color=ink, lw=.6)
    ax2.axhline(10 ** (PEAK_DBFS / 20), color=ember, lw=1, ls='--')
    ax2.axhline(-10 ** (PEAK_DBFS / 20), color=ember, lw=1, ls='--')
    ax2.text(GONG_S, 10 ** (PEAK_DBFS / 20) + .06, 'peak -3 dBFS', color=ember, fontsize=13, ha='right')
    ax2.set_xlim(0, GONG_S)
    ax2.set_ylim(-1, 1)
    ax2.set_yticks([])
    ax2.set_xticks(range(0, int(GONG_S) + 1))
    ax2.set_xticklabels([f'+{s} s' for s in range(int(GONG_S) + 1)], fontsize=13)
    for s in ('top', 'right', 'left'):
        ax2.spines[s].set_visible(False)
    ax2.set_title('The gong: two soft strikes, 10 s tail', loc='left', fontsize=15, color=muted, pad=8)
    fig.text(.95, .03, 'Made by DIVELUX — diveluxsaunahat.com', fontsize=14, color=muted, ha='right')
    fig.savefig(os.path.join(ROOT, 'preview.png'), facecolor=paper)


def main():
    g = gong()
    gw = os.path.join(ROOT, 'gong-10s.wav')
    write_wav(gw, g)
    only = [int(a) for a in sys.argv[1:]] or MINUTES
    for m in only:
        encode(gw, m)
    report = {m: check(m) for m in only}
    print(json.dumps(report, indent=1))
    preview(g)
    bad = [m for m, r in report.items() if not all(v['ok'] for v in r.values())]
    print('problems:', bad or 'none')


if __name__ == '__main__':
    main()
