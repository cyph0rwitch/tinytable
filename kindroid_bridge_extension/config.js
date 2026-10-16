// Your Kindroid companions, for this fallback extension only.
// The sidecar does not use this file. Fill in before loading the extension:
//   chatPath  the part of the Kindroid chat URL after kindroid.ai (copy it from the address bar)
//   speakers  the name Kindroid shows in "Message from <name>" (any case) mapped to the TinyTable seat
// Leave a channel's chatPath empty to ignore that chat.
// eslint-disable-next-line no-unused-vars
const TINYTABLE_KINDROID_CHANNELS = [
  {id: "kindroid:companion", type: "private", chatPath: "", speakers: {"": "kindroid-companion"}},
  {id: "kindroid:companion-2", type: "private", chatPath: "", speakers: {"": "kindroid-companion-2"}},
  {id: "kindroid:group", type: "group", chatPath: "", speakers: {}},
];
