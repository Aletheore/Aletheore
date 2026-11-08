import javax.servlet.http.Cookie;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpSession;

class SessionExamples {
    void requestValueAsAttributeName(HttpServletRequest request) {
        String name = request.getParameter("name");
        // ruleid: java-session-trust-boundary
        request.getSession().setAttribute(name, "10340");
    }

    void requestValueAsAttributeValue(HttpServletRequest request, HttpSession session) {
        String theme = request.getHeader("X-Theme");
        // ruleid: java-session-trust-boundary
        session.setAttribute("theme", theme);
    }

    void putValueForms(HttpServletRequest request, HttpSession session) {
        String key = request.getQueryString();
        // ruleid: java-session-trust-boundary
        session.putValue(key, "v");
        // ruleid: java-session-trust-boundary
        request.getSession(true).putValue("k", request.getParameter("v"));
    }

    void cookieValue(HttpServletRequest request) {
        String chosen = "default";
        for (Cookie cookie : request.getCookies()) {
            chosen = cookie.getValue();
        }
        // ruleid: java-session-trust-boundary
        request.getSession().setAttribute(chosen, "x");
    }

    void springAnnotatedParameters(@RequestParam String name, @PathVariable String id, HttpSession session) {
        // ruleid: java-session-trust-boundary
        session.setAttribute("name", name);
        // ruleid: java-session-trust-boundary
        session.setAttribute(id, "x");
    }

    void qualifiedTypes(javax.servlet.http.HttpServletRequest request) {
        javax.servlet.http.HttpSession session = request.getSession();
        String value = request.getParameter("v");
        // ruleid: java-session-trust-boundary
        session.setAttribute("v", value);
    }

    void jakartaQualifiedTypes(jakarta.servlet.http.HttpServletRequest request) {
        jakarta.servlet.http.HttpSession session = request.getSession();
        String value = request.getParameter("v");
        // ruleid: java-session-trust-boundary
        session.setAttribute("v", value);
    }

    void constantsAreFine(HttpServletRequest request) {
        // ok: java-session-trust-boundary
        request.getSession().setAttribute("count", 1);
        // ok: java-session-trust-boundary
        request.getSession().setAttribute("user", "anonymous");
    }

    void overwrittenBeforeUse(HttpServletRequest request, HttpSession session) {
        String name = request.getParameter("name");
        name = "fixed";
        // ok: java-session-trust-boundary
        session.setAttribute(name, "x");
    }

    void unannotatedParameterIsNotARequestSource(String name, HttpSession session) {
        // ok: java-session-trust-boundary
        session.setAttribute("name", name);
    }

    void notAnHttpSession(Store store, HttpServletRequest request) {
        // ok: java-session-trust-boundary
        store.setAttribute(request.getParameter("k"), "v");
    }
}

class Store {
    void setAttribute(String key, Object value) {}
}
