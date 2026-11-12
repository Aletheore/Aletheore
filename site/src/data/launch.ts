/**
 * Hallbook launch switch. On by default since the Aletheore release that ships Hallbook (0.10.0) is out. Setting
 * NEXT_PUBLIC_HALLBOOK_LAUNCHED=0 and redeploying is the way back: the page stays reachable by its URL, noindex, but
 * leaves the nav, and the install steps say it is coming in the next release.
 *
 * The sitemap and llms.txt are static files, not driven by this flag; remove the /hallbook entries from both if you
 * switch it off.
 */
export const HALLBOOK_LAUNCHED = process.env.NEXT_PUBLIC_HALLBOOK_LAUNCHED !== "0";

/**
 * Council launch switch. Off by default: Council is a separate product with its own site (aletheorecouncil.com), and this page only
 * introduces it. While off, the page is reachable by its URL but noindex and left out of the nav. Set NEXT_PUBLIC_COUNCIL_LAUNCHED=1
 * and redeploy to link it in the nav and let search engines index it. Add /council to the sitemap and llms.txt at the same time.
 */
export const COUNCIL_LAUNCHED = process.env.NEXT_PUBLIC_COUNCIL_LAUNCHED === "1";
