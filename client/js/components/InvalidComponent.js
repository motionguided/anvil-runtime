"use strict";

import { getCssPrefix } from "@runtime/runner/legacy-features";
import PyDefUtils from "PyDefUtils";

const InvalidComponent = (pyModule) => {
    pyModule["InvalidComponent"] = PyDefUtils.mkComponentCls(pyModule, "InvalidComponent", {
        properties: [
            {
                name: "text",
                pyVal: true,
                defaultValue: Sk.builtin.str.$empty,
                set(s, e, v) {
                    v = Sk.builtin.checkNone(v) ? "" : v.toString();
                    s._anvil.elements.err.textContent = v;
                },
            },
        ],
        element: ({ text }) => (
            <div refName="outer" className={`${getCssPrefix()}invalid-component`}>
                <i refName="icon" className="glyphicon glyphicon-remove"></i>
                <div refName="err" className={`${getCssPrefix()}err`}>
                    {text.toString()}
                </div>
            </div>
        ),
    });
};

export default InvalidComponent;

/*
 * TO TEST:
 *
 *  - New props: text, width
 *
 */
