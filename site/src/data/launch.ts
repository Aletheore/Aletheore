/**
 * Hallbook launch switch. Off by default: the /hallbook page can be opened by its URL (noindex), but it is not in the
 * nav, and the install steps say it is coming in the next release, until the Aletheore release that ships Hallbook is out.
 *
 * At launch: set NEXT_PUBLIC_HALLBOOK_LAUNCHED=1 in the site's environment and redeploy, then also
 *   1. add /hallbook to public/sitemap.xml and public/llms.txt (static files, not driven by this flag).
 */
export const HALLBOOK_LAUNCHED = process.env.NEXT_PUBLIC_HALLBOOK_LAUNCHED === "1";
