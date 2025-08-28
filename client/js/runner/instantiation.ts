// An add-in to the `anvil` module that allows us to hook and control component construction

import {
    Args,
    Kws,
    Suspension,
    arrayFromIterable,
    chainOrSuspend,
    checkString,
    copyKeywordsToNamedArgs,
    isTrue,
    keywordArrayFromPyDict,
    objectRepr,
    pyCall,
    pyCallOrSuspend,
    pyDict,
    pyFalse,
    pyImportError,
    pyIterable,
    pyNone,
    pyNoneType,
    pyObject,
    pyRuntimeError,
    pyStr,
    pyTuple,
    pyValueError,
    toJs,
} from "../@Sk";
import { Component, ComponentConstructor, getDefaultDepIdForComponent } from "../components/Component";
import { isPyInstantiatorFunction, PyInstantiatorFunction } from "./component-property-utils-api";
import { data } from "./data";
import { PyModMap, anvilMod, funcFastCall } from "./py-util";

// There are two times when we might want to turn the name of a form into a constructor, at which point
// we need to disambiguate which app/dependency we should look in when we get a bare string like "Form1".
//
// If a FormTemplate is instantiating a component from a YAML description, it's simple - we should always be relative
// to the app that defines that form.
//
// If we're instantiating a component from a form property (eg a RepeatingPanel instantiating its item_template
// property), and it's a property on a component created from YAML, it should be relative to the YAML that created
// that component. For example, if a dependency defines its own CustomRepeatingPanel, and we use the
// CustomRepeatingPanel from another app, the item_template property should be looked up relative to the app whose
// YAML created the CustomRepeatingPanel, not the dependency that defined the CustomRepeatingPanel class.
//
// To do this we have a magic reach-around-the-back mechanism in Component.ts which uses global state to set the
// default dep ID on the next Component to be instantiated. When we instantiate from YAML, we use this mechanism
// to set the YAML's dep ID as the default dep ID, in case that component wants to instantiate any form properties.
export interface YamlInstantiationContext {
    requestingComponent: Component;
    fromYaml: true;
    defaultDepId: string | null;
}

interface PropertyInstantiationContext {
    requestingComponent?: Component;
    fromYaml?: false;
}
export type InstantiationContext = YamlInstantiationContext | PropertyInstantiationContext;

export const getDefaultDepIdForInstantiation = (context: InstantiationContext) =>
    context.fromYaml ? context.defaultDepId : getDefaultDepIdForComponent(context.requestingComponent);

const COMPONENT_MATCHER = /^(?:([^:]+):)?([^:]*)$/;
const ComponentMatcher = (nameSpec: string) => nameSpec.match(COMPONENT_MATCHER) as [any, string | null, string] | null;

export interface ResolvedForm {
    formName: string;
    qualifiedClassName: string;
    depId: string | null;
    logicalDepId: string | null;
}

export const resolveFormSpec = (name: string, defaultDepId: string | null): ResolvedForm => {
    const [, logicalDepId = null, formName] = ComponentMatcher(name) ?? [];
    if (!formName) {
        throw new Error(`Invalid YAML spec for form: ${name}`);
    }
    const depId = logicalDepId ? data.logicalDepIds[logicalDepId] : null;
    if (logicalDepId && !depId) {
        throw new pyValueError(`Dependency not found for ${name}`);
    }
    const appPackage = logicalDepId
        ? data.dependencyPackages[depId!]
        : defaultDepId
        ? data.dependencyPackages[defaultDepId]
        : data.appPackage;
    // console.log("Resolving", appPackage, "for dep", depId, "/", defaultDepId, "from", data.dependencyPackages, "with", data.appPackage);
    if (!appPackage) {
        throw new pyValueError("Dependency not found for: " + name);
    }
    return { qualifiedClassName: `${appPackage}.${formName}`, depId, formName, logicalDepId };
};

export const getFormClassObject = ({ qualifiedClassName }: ResolvedForm) => {
    return chainOrSuspend(Sk.importModule(qualifiedClassName, false, true), () => {
        const dots = qualifiedClassName.split(".").slice(1);
        const className = dots[dots.length - 1];

        const pyFormMod = Sk.sysmodules.quick$lookup(new pyStr(qualifiedClassName));
        if (pyFormMod) {
            return Sk.abstr.gattr(pyFormMod, new pyStr(className)) as ComponentConstructor;
        }
    });
};

export const WELL_KNOWN_PATHS = {
    LAYOUT: "__anvil layout *HV57",
};

interface InstantiationHooks {
    getAnvilComponentClass: (anvilModule: PyModMap, componentType: string) => ComponentConstructor | undefined;
    getAnvilComponentInstantiator: typeof getDefaultAnvilComponentInstantiator;
    getNamedFormInstantiator: typeof getDefaultNamedFormInstantiator;
}

export const setInstantiationHooks = (hooks: InstantiationHooks) => {
    ({ getAnvilComponentInstantiator, getAnvilComponentClass, getNamedFormInstantiator } = hooks);
};

export const getDefaultAnvilComponentInstantiator = (
    context: InstantiationContext,
    componentType: string
): ((kws?: Kws, pathStep?: string | number) => Suspension | Component) => {
    const pyComponentConstructor = anvilMod[componentType] as ComponentConstructor;
    return (kws, pathStep) => pyCallOrSuspend(pyComponentConstructor, [], kws);
};

export interface FormInstantiationFlags {
    asLayout?: true;
    preferLiveDesign?: boolean;
}

// Form instantiators carry the identity of the underlying form

export interface InstantiatorFunction {
    (kws?: Kws, pathStep?: number | string): Suspension | Component;
    anvil$instantiatorForForm: ResolvedForm | null;
}

export const getDefaultNamedFormInstantiator = (
    formSpec: ResolvedForm,
    requestingComponent?: Component,
    flags?: FormInstantiationFlags
): Suspension | InstantiatorFunction => {
    return chainOrSuspend(getFormClassObject(formSpec), (constructor) => {
        if (constructor === undefined) {
            throw new pyImportError("Failed to import form " + formSpec.formName);
        }
        const ifn = (kwargs?: Kws, pathStep?: string | number) => pyCallOrSuspend(constructor, [], kwargs);
        ifn.anvil$instantiatorForForm = formSpec;
        return ifn;
    });
};

export let getAnvilComponentClass = (anvilModule: PyModMap, componentType: string) =>
    anvilModule[componentType] as ComponentConstructor | undefined;

export let getAnvilComponentInstantiator = getDefaultAnvilComponentInstantiator;

export let getNamedFormInstantiator = getDefaultNamedFormInstantiator;

export const getFormInstantiator = (
    context: InstantiationContext,
    formSpec: pyStr | ComponentConstructor | PyInstantiatorFunction,
    flags?: FormInstantiationFlags
): Suspension | InstantiatorFunction => {
    if (checkString(formSpec)) {
        const resolvedForm = resolveFormSpec(formSpec.toString(), getDefaultDepIdForInstantiation(context));
        return getNamedFormInstantiator(resolvedForm, context.requestingComponent, flags);
    } else if (isPyInstantiatorFunction(formSpec)) {
        return formSpec.anvil$underlyingInstantiator;
    } else {
        // formSpec is a Python callable; wrap it
        const ifn = (kws?: Kws) => pyCallOrSuspend<Component>(formSpec, [], kws);
        ifn.anvil$instantiatorForForm = null as any;
        return ifn;
    }
};

// Used from Python code
export const pyInstantiateComponent = funcFastCall((args_: Args, kws_?: Kws) => {
    let requestingComponent: Component,
        component: pyStr | ComponentConstructor,
        pyArgs: pyIterable<pyObject>,
        pyKws: pyDict,
        pyEditPath: pyNoneType | pyStr,
        isEditable: boolean;

    // eslint-disable-next-line prefer-const
    [requestingComponent, component, pyArgs, pyKws, pyEditPath, isEditable] = copyKeywordsToNamedArgs(
        "instantiate_component",
        ["requesting_component", "component_to_instantiate", "args", "kwargs", "edit_path", "is_editable"],
        args_,
        kws_,
        [new pyTuple(), new pyDict(), pyNone, pyFalse]
    );

    // This validates the types for pyArgs and pyKws
    // i.e. args should be a tuple/iterable, kws should be a mapping
    const args = arrayFromIterable(pyArgs);
    const kws = keywordArrayFromPyDict(pyCall(pyDict, [pyKws]));

    // TODO when do these get used?
    isEditable = isTrue(isEditable);
    const editPath = toJs(pyEditPath);

    // TODO should we type check requestingComponent?

    return chainOrSuspend(
        checkString(component)
            ? getFormClassObject(
                  resolveFormSpec(component.toString(), getDefaultDepIdForComponent(requestingComponent))
              )
            : component,
        (constructor) => {
            if (constructor === undefined) {
                // TODO improve this error or return MkInvalidComponent
                throw new pyRuntimeError("Unable to resolve " + objectRepr(component));
            }
            return pyCallOrSuspend(constructor, args, kws);
        }
    );
});
