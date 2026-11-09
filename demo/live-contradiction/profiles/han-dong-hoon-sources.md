# 한동훈 발언 출처

이 프로필은 윤석열 대통령 탄핵에 관한 2024년 12월 5일과 12일의 공개 입장을 비교한다. 같은 시점의 사실 명제에 대한 거짓말 판정이 아니다.

[12월 5일 MBC 현장 보도](https://imnews.imbc.com/replay/2024/nwdesk/article/6663716_36515.html)는 국민의힘 최고위원회의의 탄핵안 통과 반대 발언을 싣고 있다. 준비 없는 혼란에 따른 피해를 우려한 입장이다. 계엄 옹호가 아니라는 단서도 설정에 보존했다. 기사 날짜, 원본 영상 82~92초의 성명 자막과 발언 자막, 로컬 한국어 ASR을 대조했다. 인용의 괄호는 방송사가 보충한 말이다. 12월 4일 당론 논의 날짜를 본인 발언 날짜로 쓰지 않았다.

[12월 12일 MBC 보도](https://imnews.imbc.com/news/2024/politics/article/6666421_36431.html)의 15~32초에는 한동훈 본인의 연속 발언이 있다. 조기 퇴진 불응을 이유로 즉각적인 직무 정지가 필요하다고 밝힌다. 화면에 나온 얼굴, 성명, 방송 자막과 ASR의 발언 순서를 대조했다. ASR의 조사는 오인식할 수 있어 인용문은 기사와 방송 자막을 기준으로 삼았다.

[같은 날 MBC 뉴스데스크](https://imnews.imbc.com/replay/2024/nwdesk/article/6666632_36515.html)와 [SBS 현장영상 정리](https://news.sbs.co.kr/news/endPage.do?news_id=N1007909423)는 이 기자회견이 탄핵 찬성 입장을 밝힌 자리임을 확인한다. 전 국방장관의 구속영장과 조기 퇴진 불응이라는 후속 상황이 있었다. 이 맥락을 `current.context`에 담았다. 의원총회에서 나온 별도의 발언이나 다른 사람의 목소리를 재생 구간에 합치지 않았다.

`verified_by: agent`는 위 출처 대조를 뜻한다. 사람 최종 검수가 아니다. `expected_relation`은 사후 채점용이며 판정 모델의 입력이나 화면 정답으로 사용하지 않는다.

## 미디어를 재현한다

빠른 실행은 저장소 루트에서 `uv run python demo/live-contradiction/start_demo.py --prepare-media`를 사용한다. 필요한 구간 앞에서 탐색한 뒤 정확한 발언 구간을 절단한다. 아래 전체 원본 명령은 최초 제작 과정의 기록이다. media.json의 output_media 해시는 최초 제작본의 참조값이며, 구간 캐시로 재인코딩한 결과의 바이트 일치를 약속하지 않는다.

저장소 루트에서 실행한다. `han-dong-hoon.media.json`은 다운로드 URL과 편집 구간을 기록한다. 원본 HLS는 공개 MBC 플레이어가 제공하는 주소다. 직접 제공되는 원본 MP4 다운로드 URL은 확인하지 못했다. MP4 경로명으로 직접 URL을 만들어 쓰지 않는다.

```bash
HAN_MEDIA_DIR=$(mktemp -d)
HAN_SOURCE_URL=https://video.imnews.imbc.com/video/_definst_/imnews/video.imnews.imbc.com/news/2024/politics/article/__icsFiles/afieldfile/2024/12/12/joo241212_vo2.mp4/playlist.m3u8
ffmpeg -hide_banner -loglevel error -i "$HAN_SOURCE_URL" -map 0:v:0 -map 0:a:0 -c copy -movflags +faststart "$HAN_MEDIA_DIR/han-dong-hoon-source.mp4"
ffprobe -v error -show_entries format=duration,size:stream=codec_name,width,height,channels,channel_layout -of json "$HAN_MEDIA_DIR/han-dong-hoon-source.mp4"
```

원본 확인값은 120.697324초, 1920x1080 H.264, 스테레오 AAC다. 리먹스 컨테이너의 바이트 해시는 HLS 재다운로드와 FFmpeg 버전에 따라 달라질 수 있다.

다음 명령의 `HAN_FFMPEG`는 `drawtext` 필터가 있는 실행 파일이다. `HAN_FONT_FILE`은 해당 컴퓨터에 설치된 한글 폰트의 실제 경로로 지정한다. 이 장비에서는 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`와 `/System/Library/Fonts/AppleSDGothicNeo.ttc`를 사용했다.

```bash
: "${HAN_FFMPEG:?drawtext가 있는 FFmpeg 경로를 지정한다}"
: "${HAN_FONT_FILE:?설치된 한글 폰트 경로를 지정한다}"
printf '%s\n' '자료 영상 · MBC · 2024-12-12 · 발언 뒤 정지 화면·무음' > "$HAN_MEDIA_DIR/header.txt"
printf '%s\n' '실제 발언 종료 · 판정 대기 · 정지 화면과 무음' > "$HAN_MEDIA_DIR/hold.txt"
"$HAN_FFMPEG" -hide_banner -loglevel error -y \
  -ss 15 -t 17 -i "$HAN_MEDIA_DIR/han-dong-hoon-source.mp4" \
  -vf "scale=1194:672,pad=1280:720:43:48:black,setsar=1,fps=30,tpad=stop_mode=clone:stop_duration=38,drawtext=fontfile=$HAN_FONT_FILE:textfile=$HAN_MEDIA_DIR/header.txt:fontcolor=white:fontsize=23:x=18:y=12,drawtext=fontfile=$HAN_FONT_FILE:textfile=$HAN_MEDIA_DIR/hold.txt:fontcolor=white:fontsize=27:box=1:boxcolor=black@0.8:boxborderw=12:x=(w-tw)/2:y=85:enable='gte(t,17)'" \
  -af apad -t 55 -c:v libx264 -preset fast -crf 23 -pix_fmt yuv420p \
  -c:a aac -ar 48000 -ac 2 -movflags +faststart \
  demo/live-contradiction/profiles/han-dong-hoon.mp4
```

실제 발언 구간은 17초다. 이후 38초는 마지막 프레임과 무음이다. 영상 상단에 출처 날짜와 편집 안내를 표시하고, 17초부터 정지 화면 안내를 추가했다. `편집 안내` 자막은 holder 분석에서 제외한다.

## 검증을 재현한다

```bash
ffprobe -v error -show_entries format=duration,size:stream=codec_name,width,height,r_frame_rate,pix_fmt,channels,channel_layout -of json demo/live-contradiction/profiles/han-dong-hoon.mp4
ffmpeg -hide_banner -loglevel error -i demo/live-contradiction/profiles/han-dong-hoon.mp4 -f null -
ffmpeg -hide_banner -ss 17.1 -i demo/live-contradiction/profiles/han-dong-hoon.mp4 -af volumedetect -vn -f null -
git check-ignore -v demo/live-contradiction/profiles/han-dong-hoon.mp4
git diff --check -- demo/live-contradiction/profiles
```

생성 영상은 55초, 1280x720, H.264 yuv420p, 30fps, 스테레오 AAC다. 발언 구간과 대기 구간의 대표 프레임을 확인했다. 미디어는 기존 `.gitignore`의 `*.mp4` 규칙으로 제외된다. 공개 업로드는 하지 않았다.

로컬 Whisper `ggml-base`의 한국어 ASR을 원본과 17초 발췌에 실행했다. 음성 인식 오타를 그대로 인용하지 않았다. 원본 음성을 사람이 청취한 최종 검수, 실제 검색·OpenAI 판정, 브라우저 전체 흐름은 이 미디어 작업의 검증 범위 밖이다.
