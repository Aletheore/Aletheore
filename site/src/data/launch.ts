/**
 * Hallbook launch switch. On by default since the Aletheore release that ships Hallbook (0.10.0) is out. Setting
 * NEXT_PUBLIC_HALLBOOK_LAUNCHED=0 and redeploying is the way back: the page stays reachable by its URL, noindex, but
 * leaves the nav, and the install steps say it is coming in the next release.
 *
 * The sitemap and llms.txt are static files, not driven by this flag; remove the /hallbook entries from both if you
 * switch it off.
 */
export const HALLBOOK_LAUNCHED = process.env.NEXT_PUBLIC_HALLBOOK_LAUNCHED !== "0";
