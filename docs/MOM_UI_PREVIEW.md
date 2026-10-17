# Meeting minutes UI preview

Run the frontend with `npm run dev`, then open
`http://localhost:5173/mom-preview.html`.

The standalone preview contains fictional example minutes. It demonstrates the
proposed document layout and local editing interactions; it does not read or
update backend meeting records. Example edits and approvals reset on reload.
The main application's Meeting Minutes screen still uses its existing JSON
editor until the new layout is integrated with the backend schema.

## Layout

- Desktop: a full-width summary leads into discussion on the left and decisions
  and action cards on the right, all within one viewport.
- A compact agenda sits beside the selected topic on larger screens. Narrower
  screens move it above the reading pane. Underlined section navigation switches
  between topics, plans, and questions without competing rows of boxed buttons.
- The toolbar keeps compact, matching 34px controls; headings and spacing establish
  the content hierarchy.
- Supporting transcript quotes open in a contained dialog.
- Mobile: Overview, Discussion, Decisions, and Actions switch within the same
  viewport. Long detail text scrolls inside its panel.
- Print assembles the complete example minutes, including the unselected topics.

## Verification

Browser checks passed at 1920×870, 1366×768, 1280×720, 1024×768, and 820×700,
including both example topics and all discussion tabs. The example content fits
the desktop frame without page scrolling or clipped text. Mobile section views
were checked at 390×844. Keyboard tab navigation, transcript evidence, editing,
cancellation, saving, missing owner/date labels, and approval controls were also
checked.

## Integration work remaining

Replace the sample content with validated backend minutes, map discussion topics
and supporting transcript references, and connect edits and approval to the
existing APIs. Long real meetings need pagination or contained detail views;
content must not be silently truncated to preserve the desktop layout.

`mom-preview.html` is available through the Vite development server. The default
production build still uses `index.html` as its entry point and does not include
this standalone prototype. The prototype uses Tailwind and Iconify CDNs for
design review; production integration should use the app's bundled CSS and
existing Lucide components.
