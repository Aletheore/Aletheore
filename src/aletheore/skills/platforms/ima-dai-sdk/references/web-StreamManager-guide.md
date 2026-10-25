# Google IMA DAI SDK HTML5 (Web) integration guide

This guide covers the integration of the Google IMA DAI (Dynamic Ad Insertion)
SDK for web applications to run in a desktop browser or mobile browser.

## Table of Contents

*   [Integration flow](#integration-flow) (Line 24)
    *   [Import the SDK](#import-the-sdk) (Line 26)
    *   [Initialization](#initialization) (Line 35)
*   [Request DAI streams](#request-dai-streams) (Line 55)
    *   [Request a livestream](#request-a-livestream) (Line 57)
    *   [Request a VOD stream](#request-a-vod-stream) (Line 86)
*   [Timed metadata forwarding](#timed-metadata-forwarding) (Line 121)
    *   [Passing raw ID3 frames from HLS manifest](#passing-raw-id3-frames-from-hls-manifest)
        (Line 126)
    *   [Passing custom event data from DASH manifest](#passing-custom-event-data-from-dash-manifest)
        (Line 145)
*   [Stream Event and Error Handling](#stream-event-and-error-handling)
    (Line 165)
    *   [DAI session events](#dai-session-events) (Line 173)
    *   [Ad break events](#ad-break-events) (Line 181)
    *   [Ad events](#ad-events) (Line 187)
    *   [Cleanup](#cleanup) (Line 201)
*   [Reference implementation](#reference-implementation) (Line 209)

## Integration flow

### Import the SDK

Load the IMA DAI SDK script loader at the page level. Ensure the SDK can access
`window.top.location.href`.

```html
<script src="https://imasdk.googleapis.com/js/sdkloader/ima3_dai.js"></script>
```

### Initialization

Instantiate the `StreamManager` early. It requires the HTML video element and an
HTML `div` element overlaying the video element for the ad UI elements.

```html
<div id="player-container">
  <video id="video-element" controls></video>
  <div id="ad-ui-element"></div>
</div>
```

```typescript
const videoElement = document.getElementById('video-element') as HTMLVideoElement;
const adUiElement = document.getElementById('ad-ui-element') as HTMLElement;

// Instantiate the StreamManager
const streamManager = new google.ima.dai.api.StreamManager(videoElement, adUiElement);
```

## Request DAI streams

### Request a livestream

Instantiate `google.ima.dai.api.LiveStreamRequest` to request a Google DAI
linear stream using the default HLS M3U8 format.

```typescript
const hlsLiveStreamRequest = new google.ima.dai.api.LiveStreamRequest();
hlsLiveStreamRequest.assetKey = <ASSET_KEY_PLACEHOLDER>;
hlsLiveStreamRequest.networkCode = <NETWORK_CODE_PLACEHOLDER>;
streamManager.requestStream(hlsLiveStreamRequest);
```

To request a Google DAI linear stream using the DASH MPD format, ensure to
explicitly set the `LiveStreamRequest.format` property as follows:

```typescript
const dashLiveStreamRequest = new google.ima.dai.api.LiveStreamRequest();
dashLiveStreamRequest.assetKey = <ASSET_KEY_PLACEHOLDER>;
dashLiveStreamRequest.networkCode = <NETWORK_CODE_PLACEHOLDER>;
dashLiveStreamRequest.format = 'dash';
streamManager.requestStream(dashLiveStreamRequest);
```

Provide the following parameters:

*   **<NETWORK_CODE_PLACEHOLDER>**: The Google Ad Manager network code.
*   **<ASSET_KEY_PLACEHOLDER>**: The livestream asset key configured in Google
    Ad Manager.

### Request a VOD stream

Instantiate `google.ima.dai.api.VODStreamRequest` to request a Google DAI VOD
stream using the default HLS M3U8 format.

```typescript
const hlsVodStreamRequest = new google.ima.dai.api.VODStreamRequest();
hlsVodStreamRequest.networkCode = <NETWORK_CODE_PLACEHOLDER>;
hlsVodStreamRequest.contentSourceId = <CONTENT_SOURCE_ID_PLACEHOLDER>;
hlsVodStreamRequest.videoId = <VIDEO_ID_PLACEHOLDER>;
streamManager.requestStream(hlsVodStreamRequest);
```

To request a Google DAI VOD stream using the DASH MPD format, set the
`VODStreamRequest.format` property as follows:

```typescript
const dashVodStreamRequest = new google.ima.dai.api.VODStreamRequest();
dashVodStreamRequest.networkCode = <NETWORK_CODE_PLACEHOLDER>;
dashVodStreamRequest.contentSourceId = <CONTENT_SOURCE_ID_PLACEHOLDER>;
dashVodStreamRequest.videoId = <VIDEO_ID_PLACEHOLDER>;
dashVodStreamRequest.format = 'dash';
streamManager.requestStream(dashVodStreamRequest);
```

Provide the following required parameters:

*   **<NETWORK_CODE_PLACEHOLDER>**: The Google Ad Manager network code.
*   **<CONTENT_SOURCE_ID_PLACEHOLDER>**: The content source ID (CMS ID) in
    Google Ad Manager.
*   **<VIDEO_ID_PLACEHOLDER>**: The video ID in the CMS.

For testing purposes, use values of DAI sample streams from
https://developers.google.com/ad-manager/dynamic-ad-insertion/streams.md.txt?utm_source=agent-skills&utm_medium=content&utm_campaign=adr-ss-ai&utm_content=ima-dai-sdk

## Timed metadata forwarding

For the SDK to trigger ad events, ensure to listen to the video player's events
to extract the timed metadata and pass it to the SDK for immediate processing.

### Passing raw ID3 frames from HLS manifest

Capture and pass the embedded ID3 metadata to the SDK for processing.

If HLS.js is used, listen for the `FRAG_PARSING_METADATA` event.

```typescript
import Hls, { FragParsingMetadataData } from 'hls.js';

hls.on(Hls.Events.FRAG_PARSING_METADATA, (event: Events.FRAG_PARSING_METADATA, data: FragParsingMetadataData): void => {
  // Iterate over each parsed metadata sample
  data.samples.forEach((sample) => {
    // sample.data: Uint8Array containing raw ID3 data
    // sample.pts: Presentation timestamp for precise synchronization
    streamManager.processMetadata('ID3', sample.data, sample.pts);
  });
});
```

### Passing custom event data from DASH manifest

If DASH.js is used, listen for the DASH custom event identified by the Google
DAI scheme ID and pass the event payload data to the SDK for processing.

```typescript
interface DashEventPayload {
  event: {
    messageData: Uint8Array;
    calculatedPresentationTime: number;
  };
}

dashPlayer.on('urn:google:dai:2018', (payload: DashEventPayload): void => {
    const mediaId = payload.event.messageData;
    const pts = payload.event.calculatedPresentationTime;
    streamManager.processMetadata('urn:google:dai:2018', mediaId, pts);
  });
```

## Stream Event and Error Handling

Add listeners on `google.ima.dai.api.StreamManager` for all event types:

*   DAI session events and errors
*   Ad break events
*   Individual ad events

### DAI session events

Listen for the `google.ima.dai.api.StreamEvent.Type.LOADED` event to retrieve
and pass the `event.getStreamData().url` string to the video player.

Listen for the `google.ima.dai.api.StreamEvent.Type.ERROR` event to log the
`event.getStreamData().errorMessage` string and switch to a fallback stream.

### Ad break events

Listen for the `google.ima.dai.api.StreamEvent.Type.AD_PERIOD_STARTED` and
`google.ima.dai.api.StreamEvent.Type.AD_PERIOD_ENDED` events to disable and
restore playback controls, such as seeking.

### Ad events

Listen for the following events of individual ads to log them:

*   `google.ima.dai.api.StreamEvent.Type.STARTED`
*   `google.ima.dai.api.StreamEvent.Type.FIRST_QUARTILE`
*   `google.ima.dai.api.StreamEvent.Type.MIDPOINT`
*   `google.ima.dai.api.StreamEvent.Type.THIRD_QUARTILE`
*   `google.ima.dai.api.StreamEvent.Type.COMPLETE`
*   `google.ima.dai.api.StreamEvent.Type.CLICK`: When this click-through event
    occurs, the SDK pauses the ad playback and takes the user to the ad landing
    page. Ensure to prepare the app's UI for the user to resume ad playback upon
    returning from the ad landing page.

### Cleanup

Reset the `StreamManager` when a stream is ended, or a fatal error occurs.

```typescript
streamManager.reset();
```

## Reference implementation

HLS.js integration:

*   [HTML page](https://raw.githubusercontent.com/googleads/googleads-ima-html5-dai/refs/heads/main/hls_js/simple/dai.html)
*   [App logic](https://raw.githubusercontent.com/googleads/googleads-ima-html5-dai/refs/heads/main/hls_js/simple/dai.js)

DASH.js integration:

*   [HTML page](https://raw.githubusercontent.com/googleads/googleads-ima-html5-dai/refs/heads/main/dash_js/simple/dai.html)
*   [App logic](https://raw.githubusercontent.com/googleads/googleads-ima-html5-dai/refs/heads/main/dash_js/simple/dai.js)
