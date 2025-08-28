import { chainOrSuspend, promiseToSuspension, proxy, suspensionToPromise, toPy, Suspension } from "@Sk";
import type { Component } from "@runtime/components/Component";
import type { JsComponent, RawJsComponent, WrappedJsComponent } from "./component";
import { PY_COMPONENT, JS_COMPONENT } from "./constants";

export const assert = (condition: any, msg?: string) => {
    if (condition) return;
    throw new Error(msg ?? "fail");
};

export const fail = (msg?: string) => {
    throw new Error(msg ?? "fail");
};

export const toPyComponent = (jsComponent: JsComponent): Component => {
    const pyComponent = (jsComponent as RawJsComponent)[PY_COMPONENT];
    assert(pyComponent, "found a JsComponent without a py component wrapper");
    return pyComponent;
};

const pyComponents = new WeakMap<Component, WrappedPyComponent>();

function isSuspension(obj: any): obj is Suspension {
    return obj instanceof Suspension;
}

export class WrappedPyComponent implements RawJsComponent {
    [PY_COMPONENT]: Component;
    constructor(component: Component) {
        this[PY_COMPONENT] = component;
    }
    _anvilSetupDom() {
        const rv = this[PY_COMPONENT].anvil$hooks.setupDom();
        if (isSuspension(rv)) {
            return suspensionToPromise(() => rv as Suspension | HTMLElement);
        }
        return rv;
    }
    get _anvilDomElement() {
        return this[PY_COMPONENT].anvil$hooks.domElement;
    }
}

const hasJsComponent = (component: any): component is WrappedJsComponent => !!component[JS_COMPONENT];

export function asJsComponent(component: Component): JsComponent {
    if (hasJsComponent(component)) {
        return component[JS_COMPONENT];
    }
    let wrapped = pyComponents.get(component);
    if (wrapped === undefined) {
        wrapped = new WrappedPyComponent(component);
        pyComponents.set(component, wrapped);
    }
    return wrapped;
}

export const returnToPy = (rv: any) => {
    if (rv instanceof Promise) {
        return chainOrSuspend(promiseToSuspension(rv), toPy);
    }
    return proxy(rv);
};

export const maybeSuspend = (rv: any) => {
    if (rv instanceof Promise) {
        return promiseToSuspension(rv);
    }
    return rv;
};
