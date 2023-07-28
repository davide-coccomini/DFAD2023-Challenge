from PIL import Image
from IPython.display import display
import torch as th
import os
from glide_text2im.download import load_checkpoint
from glide_text2im.model_creation import (
    create_model_and_diffusion,
    model_and_diffusion_defaults,
    model_and_diffusion_defaults_upsampler
)


def save_images(batch: th.Tensor, output_path, prompt):
    scaled = ((batch + 1)*127.5).round().clamp(0,255).to(th.uint8).cpu()
    reshaped = scaled.permute(0, 2, 3, 1)
    for index, image in enumerate(reshaped):
        img = Image.fromarray(image.numpy())
        img.save(os.path.join(output_path, prompt, str(index) + ".png"))



# Create a classifier-free guidance sampling function
def model_fn(x_t, ts, **kwargs):
    half = x_t[: len(x_t) // 2]
    combined = th.cat([half, half], dim=0)
    model_out = model_gl(combined, ts, **kwargs)
    eps, rest = model_out[:, :3], model_out[:, 3:]
    cond_eps, uncond_eps = th.split(eps, len(eps) // 2, dim=0)
    half_eps = uncond_eps + guidance_scale * (cond_eps - uncond_eps)
    eps = th.cat([half_eps, half_eps], dim=0)
    return th.cat([eps, rest], dim=1)



guidance_scale = 3.0
# Create base model_gl.
options = model_and_diffusion_defaults()
options['use_fp16'] = True
options['timestep_respacing'] = '100' # use 100 diffusion steps for fast sampling
model_gl, diffusion = create_model_and_diffusion(**options)
model_gl.eval()


# Create upsampler model_gl.
options_up = model_and_diffusion_defaults_upsampler()
options_up['use_fp16'] = True
options_up['timestep_respacing'] = 'fast27' # use 27 diffusion steps for very fast sampling
model_up, diffusion_up = create_model_and_diffusion(**options_up)
model_up.eval()

def txt2img(prompts, skip_grid=True, skip_save=False, n_samples=1, outdir="", device=0):
    

    
    model_gl.convert_to_fp16()
    model_gl.to(device)
    model_gl.load_state_dict(load_checkpoint('base', device))
    model_up.convert_to_fp16()
    model_up.to(device)
    model_up.load_state_dict(load_checkpoint('upsample', device))

    # Sampling parameters
    batch_size = n_samples

    # Tune this parameter to control the sharpness of 256x256 images.
    # A value of 1.0 is sharper, but sometimes results in grainy artifacts.
    upsample_temp = 0.997

    ##############################
    # Sample from the base model #
    ##############################

    rows = len(prompts)
    for index, prompt in enumerate(prompts):
        # Create the text tokens to feed to the model_gl.
        tokens = model_gl.tokenizer.encode(prompt)
        tokens, mask = model_gl.tokenizer.padded_tokens_and_mask(
            tokens, options['text_ctx']
        )

        # Create the classifier-free guidance tokens (empty)
        full_batch_size = batch_size * 2
        uncond_tokens, uncond_mask = model_gl.tokenizer.padded_tokens_and_mask(
            [], options['text_ctx']
        )

        # Pack the tokens together into model kwargs.
        model_kwargs = dict(
            tokens=th.tensor(
                [tokens] * batch_size + [uncond_tokens] * batch_size, device=device
            ),
            mask=th.tensor(
                [mask] * batch_size + [uncond_mask] * batch_size,
                dtype=th.bool,
                device=device,
            ),
        )

        # Sample from the base model_gl.
        model_gl.del_cache()
        samples = diffusion.p_sample_loop(
            model_fn,
            (full_batch_size, 3, options["image_size"], options["image_size"]),
            device=device,
            clip_denoised=True,
            progress=True,
            model_kwargs=model_kwargs,
            cond_fn=None,
        )[:batch_size]
        model_gl.del_cache()

        # Show the output


        ##############################
        # Upsample the 64x64 samples #
        ##############################

        tokens = model_up.tokenizer.encode(prompt)
        tokens, mask = model_up.tokenizer.padded_tokens_and_mask(
            tokens, options_up['text_ctx']
        )

        # Create the model conditioning dict.
        model_kwargs = dict(
            # Low-res image to upsample.
            low_res=((samples+1)*127.5).round()/127.5 - 1,

            # Text tokens
            tokens=th.tensor(
                [tokens] * batch_size, device=device
            ),
            mask=th.tensor(
                [mask] * batch_size,
                dtype=th.bool,
                device=device,
            ),
        )

        # Sample from the base model_gl.
        model_up.del_cache()
        up_shape = (batch_size, 3, options_up["image_size"], options_up["image_size"])
        up_samples = diffusion_up.ddim_sample_loop(
            model_up,
            up_shape,
            noise=th.randn(up_shape, device=device) * upsample_temp,
            device=device,
            clip_denoised=True,
            progress=True,
            model_kwargs=model_kwargs,
            cond_fn=None,
        )[:batch_size]
        model_up.del_cache()

        # Save the output
        '''
        save_images(up_samples, outdir, prompt)

        if index % 100:
            print("Generated", str(index), "/", str(rows))
        '''
        scaled = ((batch + 1)*127.5).round().clamp(0,255).to(th.uint8).cpu()
        reshaped = scaled.permute(0, 2, 3, 1)
        img = Image.fromarray(reshaped[0].numpy())
        return img